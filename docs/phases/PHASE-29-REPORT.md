# PHASE-29-REPORT — Experience IDE through App Server

## What was implemented

Phase 29 adds the missing IDE Experience client from the system architecture: Web,
IDE, CLI, and API all terminate at one App Server and one Harness. The extension does
not add an intelligence path and does not open production systems.

- `apps/ide-extension` provides VS Code commands for `ask`, Secret Storage token
  management, Run cancel/replay, and capability approvals.
- Default `obsion.protocol` is App Server JSON-RPC with durable `client_request_id`
  idempotency. `rest` uses the same application services over HTTP when a WebSocket
  transport is unavailable.
- Contributed settings may store `baseUrl` and `protocol` only. Credentials are
  rejected if present; VS Code Secret Storage and `OBSION_TOKEN` are the supported
  secret channels.
- TypeScript SDK helpers `appServerUrlFromApiUrl` and `newClientRequestId` are shared
  with the Workbench.
- ADR 0008 records that Experience clients must not implement Harness.

## Architecture decisions

Runtime, render, and command modules are host-agnostic. `extension.ts` is the only
`vscode` import so unit tests can exercise ask/cancel/replay/approve without the
extension host. Wait-for-run uses the durable Event projection rather than a local
Agent loop. Answer text is reconstructed from `answer.delta` events, then Artifact
markdown.

## Validation

- `uv run pytest --no-cov` — 439 passed, 18 opt-in PostgreSQL tests skipped, including
  `test_phase29_experience_ide.py`.
- TypeScript SDK tests — 18 passed, including App Server URL derivation and
  `newClientRequestId`.
- `@obsion/ide-extension` tests — 10 passed (architecture, App Server ask, REST
  workspace create, Secret Storage, cancel/replay, pending approvals).
- Architecture tests: sources do not import control-plane modules; only `extension.ts`
  imports `vscode`; package.json contributes no credential settings.
- `uv run ruff check .` — 0 findings.
- `uv run obsion scan-secrets` — 0 findings.

## Remaining risks

- Live VS Code host packaging against a remote cluster is operator-owned; in-process
  coverage uses a scripted App Server transport and REST.
- IM adapters remain a future Experience client on the same protocol.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
