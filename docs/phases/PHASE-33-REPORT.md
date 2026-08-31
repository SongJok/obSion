# PHASE-33-REPORT — Experience Desktop

## What was implemented

Phase 33 adds `obsion-desktop` as an Experience client of one App Server. It does not
implement a second Harness and does not store credentials in config files.

- `apps/desktop` provides `obsion-desktop ask` and `obsion-desktop serve`.
- The serve path binds a loopback HTML shell on `127.0.0.1` for ask, Evidence, Claims,
  approvals, cancel, and replay.
- `electron-main.ts` is the optional native window host and may only load that
  loopback URL.
- Tokens are stored in `~/.config/obsion/desktop.secret` (mode `0600`) or taken from
  `OBSION_TOKEN`.
- ADR 0012 records that Desktop is an Experience client, not a bot runtime.

## Architecture decisions

Electron is a window host, not a required CI dependency. The App Server contract is
proven without downloading a browser binary. Runtime, session, and the loopback server
never import Electron.

## Validation

- `uv run pytest --no-cov` — 473 passed, 18 opt-in PostgreSQL tests skipped, including
  `test_phase33_experience_desktop.py`.
- `@obsion/desktop` tests — 16 passed (architecture, App Server ask, REST workspace
  create, secret file, loopback UI, host rejection, cancel/replay, approvals).
- Tokens do not appear in `/api/status` or ask responses.

## Remaining risks

- Packaging Electron for a signed desktop installer remains operator-owned.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
- Vendor IM HTTP POST still requires a real tenant application.
