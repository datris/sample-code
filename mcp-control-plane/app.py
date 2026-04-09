import uuid
import logging
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()
logger = logging.getLogger("control_plane")
logging.basicConfig(level=logging.INFO)

# --- Tool registry: maps role → action → MCP server URL ---
# The agent sees tool names only. Server URLs stay here.

TOOL_REGISTRY = {
    "analyst": {
        "read_positions": "http://mcp-marketdata:8001",
        "run_report":     "http://mcp-research:8003",
    },
    "trader": {
        "read_positions": "http://mcp-marketdata:8001",
        "execute_trade":  "http://mcp-execution:8002",
    },
}

POLICY_RULES = {
    ("execute_trade", "production"): False,  # hard block
}

def get_scoped_token(agent_id: str, action: str, mcp_url: str) -> str:
    # In production: call Vault or AWS STS, scoped to this server
    return f"tok_{agent_id}_{action}_{uuid.uuid4().hex[:8]}"

# --- Request models ---

class DiscoveryRequest(BaseModel):
    agent_id:   str
    agent_role: str

class ToolRequest(BaseModel):
    agent_id:    str
    agent_role:  str
    action:      str
    environment: str
    payload:     dict = {}

# --- 1. Discovery endpoint ---
# Returns tool names only. No URLs. No routing info.
# The agent builds its tool definitions from this list.

@app.post("/tools")
def get_tools(req: DiscoveryRequest):
    role_tools = TOOL_REGISTRY.get(req.agent_role, {})
    tool_names = list(role_tools.keys())
    logger.info({"agent_id": req.agent_id, "role": req.agent_role,
                 "tools_returned": tool_names})
    return {"tools": tool_names}

# --- 2. Invocation endpoint ---
# Resolves the MCP server, enforces policy, mints a token,
# and calls the MCP server. The agent has no direct route to any of this.

@app.post("/invoke")
def invoke_tool(req: ToolRequest):
    trace_id = uuid.uuid4().hex

    role_tools = TOOL_REGISTRY.get(req.agent_role, {})

    # Discovery check — is this tool in this agent's allowed list?
    if req.action not in role_tools:
        logger.info({"trace_id": trace_id, "agent_id": req.agent_id,
                     "action": req.action, "result": "denied_discovery"})
        raise HTTPException(status_code=403, detail="Tool not available for this role")

    # Policy check — hard rules, no exceptions
    if POLICY_RULES.get((req.action, req.environment)) is False:
        logger.info({"trace_id": trace_id, "agent_id": req.agent_id,
                     "action": req.action, "result": "denied_policy"})
        raise HTTPException(status_code=403, detail="Action blocked by policy")

    # Resolve the MCP server for this action
    mcp_url = role_tools[req.action]

    # Mint a short-lived token scoped to this server and action
    token = get_scoped_token(req.agent_id, req.action, mcp_url)

    # Control plane calls the MCP server — not the agent
    response = requests.post(
        f"{mcp_url}/invoke",
        json={"action": req.action, "payload": req.payload},
        headers={"Authorization": f"Bearer {token}",
                 "X-Trace-Id": trace_id}
    )
    result = response.json()

    # Structured audit log — includes which MCP server was called
    logger.info({"trace_id": trace_id, "agent_id": req.agent_id,
                 "action": req.action, "mcp_server": mcp_url,
                 "environment": req.environment,
                 "result": "allowed", "status": result["status"]})

    return {"trace_id": trace_id, "result": result}