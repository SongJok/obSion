# PHASE-76-REPORT — Feishu live validation

## What was implemented

- Added an explicit pytest `live` marker and `make validate-feishu-live` with required
  opt-in and environment credential checks.
- Bounded the target to three non-sending Feishu probes: IM token health, fixed
  nonexistent document denial, and wiki-space read/denial.
- Corrected `FeishuDocsClient` to parse size-bounded structured error JSON before
  generic non-2xx classification.
- Normalized Feishu business codes `99992402` (missing/inaccessible document) and
  `99991672` (wiki permission) to the same denied contract, avoiding an ACL existence
  oracle.
- Added unit/architecture tests, operator instructions, ADR 0055, and the Phase 76
  architecture gate.

## Architecture decisions

Live validation is an opt-in connector adapter smoke, not a second production
execution path. It does not send, ingest, persist, emit Capability Evidence, or claim
end-to-end tenant acceptance. Normal external work remains Gateway/Policy/ACL/Audit
governed. Credentials remain in a single child-process environment and are never
printed or loaded from repository files.

## Migration

No database, Event, API, Agent, Skill, model, or connector manifest migration is
required.

## Validation

- Real tenant `make validate-feishu-live` — 3 passed, 801 deselected; no message or
  document write occurred.
- Initial live run proved IM authentication but exposed Knowledge HTTP 400
  misclassification; the root cause was fixed and the same live target then passed.
- Offline focused suite — 35 passed, 3 live tests skipped by default.
- `make check` — passed: Ruff format/lint, strict mypy over 200 source files,
  contracts/evaluations/release notes/secret scan, frontend lint/typecheck, 782
  Python tests passed with 22 documented opt-in/live skips, 50 Desktop/IDE/
  TypeScript SDK tests passed, and Alembic reported no drift.
- Existing PostgreSQL opt-in integration suite remained 15 passed with 2 historical
  destructive migration cases behind their dedicated switches; Phase 76 adds no
  database change.

## Remaining risks

- The available credentials prove authentication but do not by themselves prove every
  docx/wiki/drive scope or any specific document ACL.
- No permitted real document id was supplied, so end-to-end live ingest/search/
  citation through Capability Gateway remains a separate tenant validation.
- Public callbacks, real message delivery, staging, and human security/data-owner
  sign-off remain operator-owned.
