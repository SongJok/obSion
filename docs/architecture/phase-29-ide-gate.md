# Phase 29 Experience IDE review

## Review question

Can engineers drive Workspace → Thread → Turn → Run from a VS Code extension that
uses the existing App Server and REST surfaces, without a second Harness, without
storing credentials in settings, and without weakening Policy, Evidence, or
production read-only defaults?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `@obsion/ide-extension` lives in `apps/ide-extension` and depends on `@obsion/sdk`
  only.
- Default protocol is App Server JSON-RPC for Thread/Turn/Run/Approval mutations.
- Workspace create, Evidence, Claims, Steps, and Artifact bodies stay on REST.
- `obsion.ask` waits on the durable Run Event stream and prints timeline, answer,
  Claims, and Evidence. Tokens never appear in rendered output.
- Contributed settings are only `obsion.baseUrl` and `obsion.protocol`. Credential
  keys are rejected. Secret Storage or `OBSION_TOKEN` supplies the bearer.
- `vscode` is imported only from `extension.ts`. Runtime tests do not load the VS Code
  module.
- Architecture tests forbid control-plane, Harness, Model Gateway, and SQLAlchemy
  strings from `apps/ide-extension/src`.

## Automated acceptance map

- `apps/ide-extension/tests/architecture.test.mjs` forbids a second runtime and
  credential settings.
- `apps/ide-extension/tests/runtime.test.mjs` covers App Server thread/turn mutations
  and REST workspace create.
- `apps/ide-extension/tests/commands.test.mjs` covers Secret Storage, ask rendering,
  cancel/replay, and pending approvals.
- `services/control-plane/tests/test_phase29_experience_ide.py` repeats the client
  boundary from Python CI.

## Human review checklist

- Confirm operator token distribution into VS Code Secret Storage or `OBSION_TOKEN`.
- Confirm WebSocket ingress to `/api/v1/app-server` before requiring App Server as
  the only supported mode.
- Confirm the extension host used in staging is VS Code 1.100 or newer so the ESM
  package loads.
