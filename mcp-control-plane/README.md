# MCP Control Plane

A small FastAPI service that sits between AI agents and a fleet of MCP (Model Context Protocol) servers. Agents never see MCP server URLs or hold long-lived credentials — they ask the control plane what tools they can use, then ask the control plane to invoke them. The control plane handles discovery, role-based access, policy enforcement, short-lived token minting, and audit logging.

This is a reference implementation meant to illustrate the pattern. It is not production-ready.

## What it does

The service exposes two endpoints:

### `POST /tools` — discovery
Given an `agent_id` and `agent_role`, returns the list of tool **names** that role is allowed to call. No URLs, no routing info. The agent uses this list to build its own tool definitions.

```json
{ "agent_id": "a1", "agent_role": "analyst" }
```
→
```json
{ "tools": ["read_positions", "run_report"] }
```

### `POST /invoke` — invocation
The agent asks the control plane to run a tool. The control plane:

1. Confirms the tool is in the agent's role allow-list (discovery check).
2. Applies hard policy rules (e.g. `execute_trade` is blocked in `production`).
3. Resolves the action to a concrete MCP server URL from the registry.
4. Mints a short-lived, server-scoped token.
5. Calls the MCP server on the agent's behalf.
6. Writes a structured audit log line with a trace ID.

```json
{
  "agent_id": "a1",
  "agent_role": "trader",
  "action": "execute_trade",
  "environment": "staging",
  "payload": { "symbol": "ACME", "qty": 10 }
}
```

The agent never learns the MCP server URL and never holds a token that works against more than one server.

## Configuration

Everything lives in [app.py](app.py):

- `TOOL_REGISTRY` — maps `role → action → MCP server URL`. Edit this to add roles or wire up real servers.
- `POLICY_RULES` — hard `(action, environment)` blocks. `False` means deny.
- `get_scoped_token()` — stub that returns a fake token. Replace with a real call to Vault, AWS STS, or your auth system.

## Running it

The project uses a local virtualenv (`my-env/`).

```sh
# activate the venv
source my-env/bin/activate

# install dependencies (first time only)
pip install fastapi uvicorn requests pydantic

# run the server
uvicorn app:app --host 0.0.0.0 --port 8000
```

If `uvicorn` isn't on your PATH after install, run it via the module form to make sure you're using the venv's Python:

```sh
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

For local development with auto-reload on file changes:

```sh
uvicorn app:app --reload
```

The service listens on `http://localhost:8000`. Interactive API docs are at `http://localhost:8000/docs`.

## Quick test

With the server running:

```sh
# discover tools for the analyst role
curl -s -X POST http://localhost:8000/tools \
  -H 'content-type: application/json' \
  -d '{"agent_id":"a1","agent_role":"analyst"}'

# try to invoke a blocked action — should 403
curl -s -X POST http://localhost:8000/invoke \
  -H 'content-type: application/json' \
  -d '{"agent_id":"a1","agent_role":"trader","action":"execute_trade","environment":"production","payload":{}}'
```

The `/invoke` calls will fail to reach the upstream MCP servers unless you actually have something running at the URLs in `TOOL_REGISTRY` — the discovery and policy checks run first, so you can exercise those without any backend.

## Keeping it running

For anything beyond a foreground terminal session:

- **Mac, background:** wrap the `uvicorn` command in a `launchd` plist under `~/Library/LaunchAgents/` with `KeepAlive=true`.
- **Linux server:** a `systemd` unit with `Restart=always`.
- **Container:** `CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]` and run with `--restart unless-stopped`.
- **Multiple workers / production:** `gunicorn -k uvicorn.workers.UvicornWorker app:app -w 4`.
