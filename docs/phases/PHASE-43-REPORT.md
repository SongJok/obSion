# PHASE-43-REPORT — AGENT in-process Gateway transport

## What was implemented

Phase 43 installs AGENT as a Capability Gateway transport. It is a protocol behind
the gateway, not a second Harness and not a conversation Agent picker.

- `DevelopmentAgentExecutor` encodes `{agent, operation, input}` and dispatches to
  in-process handlers.
- Development connector `obsion-agent-development` / capability
  `agent.development.echo` is seeded for local catalog use. No AgentSpec declares it.
- Nested Harness, remote agent URLs, spawn/sidecar, and non-empty egress fail
  closed (`capability_transport_unavailable`). Registry manifests with those shapes
  are rejected.
- Connector credentials are not copied into the invocation envelope.
- ADR 0022 records the in-process boundary. Specialist routing stays on
  Understanding and AgentRouter. No schema migration.

## Architecture decisions

AGENT stays behind Policy, grants, rate limits, schema validation, Evidence, and
audit. This is not a nested child Run. Vendor IM HTTP is still not implemented.

## Validation

- `uv run pytest --no-cov` — 560 passed, 18 opt-in PostgreSQL tests skipped,
  including `test_phase43_agent_transport.py`.
- Architecture AST: `capabilities/agent.py` does not import Harness,
  AutomationWorker, HTTP clients, or subprocess. `harness/runtime.py` and
  `automation/worker.py` do not import the agent executor.
- Workbench 治理控制台 catalog copy includes
  `MCP/SDK/gRPC/Workflow/Agent 为进程内适配器`. Connector health lists
  `obsion-agent-development`. Composer still has one prompt and no Agent picker.

## Remaining risks

- Nested child Runs would need an explicit recursion budget; they are not
  implemented here.
- Public IM webhook hosting, WeCom AES decrypt, and vendor HTTP POST still require
  a real tenant application.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
- Signed `1.0.0` remains operator-owned.
