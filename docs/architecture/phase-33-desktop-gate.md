# Phase 33 Experience Desktop review

## Review question

Can operators drive Workspace → Thread → Turn → Run from a repository desktop client
that uses the existing App Server and REST surfaces, without a second Harness, without
storing credentials in config files, and without loading a native window from a
non-loopback URL?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `@obsion/desktop` lives in `apps/desktop` and depends on `@obsion/sdk` only.
- Default protocol is App Server JSON-RPC for Thread/Turn/Run/Approval mutations.
- Workspace create, Evidence, Claims, Steps, and Artifact bodies stay on REST.
- The loopback desktop shell shows timeline, answer, Claims, and Evidence. Tokens
  never appear in rendered output or `/api/status`.
- Config JSON cannot contain credential keys. The bearer is `desktop.secret` mode
  `0600` or `OBSION_TOKEN`.
- Electron is imported only from `electron-main.ts`. That host may only load
  `http://127.0.0.1`. The window server may only bind `127.0.0.1`.
- Architecture tests forbid control-plane, Harness, Model Gateway, and SQLAlchemy
  strings from `apps/desktop/src`.

## Automated acceptance map

- `apps/desktop/tests/architecture.test.mjs` forbids a second runtime and credential
  package settings.
- `apps/desktop/tests/runtime.test.mjs` covers App Server thread/turn mutations and
  REST workspace create.
- `apps/desktop/tests/session.test.mjs` covers secret-file tokens, loopback UI, host
  rejection, cancel/replay, and approvals.
- `apps/desktop/tests/secrets.test.mjs` covers owner-only secret files.
- `services/control-plane/tests/test_phase33_experience_desktop.py` repeats the
  client boundary from Python CI.

## Human review checklist

- Confirm operator token distribution into `desktop.secret` or `OBSION_TOKEN`.
- Confirm WebSocket ingress to `/api/v1/app-server` before requiring App Server as
  the only supported mode.
- Confirm Electron, when installed, is used only as a window host for the loopback
  shell.
