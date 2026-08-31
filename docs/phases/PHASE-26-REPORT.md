# PHASE-26-REPORT — Experience CLI through App Server

## What was implemented

Phase 26 adds the missing Experience client from the system architecture: Web, IDE,
CLI, and API all terminate at one App Server and one Harness. The CLI does not add an
intelligence path and does not open production systems.

- `apps/cli` provides `obsion-cli` for workspace, thread lifecycle, `ask`, run
  inspect/cancel/replay, Evidence/Claims/Artifacts, and capability approvals.
- Default `--protocol app-server` sends Thread/Turn/Run/Approval mutations as
  JSON-RPC with durable `client_request_id` idempotency. `--protocol rest` uses the
  same application services over HTTP when a WebSocket transport is unavailable.
- Config files may store `base_url` and `protocol` only. Credentials are rejected if
  present; `OBSION_TOKEN` is the supported secret channel.
- Python and TypeScript SDKs now wrap the remaining App Server methods
  (`workspace.list`, thread lifecycle, run get/cancel/replay/events, approvals,
  artifacts) plus REST `/api/v1/approvals`.
- ADR 0005 records that Experience clients must not implement Harness.

## Architecture decisions

The operator command `obsion` remains control-plane tooling (`serve`, contract
validation, secret scan, SBOM). The employee/engineer command is `obsion-cli`. This
avoids collapsing a user client into the control-plane process and keeps the CLI
installable from `obsion-sdk` without importing FastAPI, SQLAlchemy, or connectors.

Wait-for-run uses the durable Event projection (REST `run.events` or App Server
`run.get`) rather than a local Agent loop. Answer text is reconstructed from
`answer.delta` events, then Artifact markdown. Claims and Evidence are fetched after
the Run is terminal so Critic publication is already recorded.

## Validation

- `uv run pytest` — 433 passed, 18 opt-in PostgreSQL tests skipped, including
  `apps/cli/tests` and `test_phase26_experience_cli.py`.
- TypeScript SDK tests — 17 passed, including App Server method coverage and REST
  `/api/v1/approvals`.
- Architecture AST test: CLI sources do not import `obsion.*` control-plane modules.
- Greeting e2e: CLI runtime `ask 你好` persists OBSERVE → UNDERSTAND → PLAN →
  VERIFY → RESPOND and `run.completed` without leaking the bearer token.
- `uv run obsion scan-secrets` — 0 findings.

## Remaining risks

- Live WebSocket connectivity against a remote cluster is operator-owned; in-process
  greeting coverage uses REST reconciliation, while App Server mutation coverage uses
  a scripted transport.
- IDE and IM adapters remain future Experience clients on the same protocol.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
