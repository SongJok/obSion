# PHASE-30-REPORT — Experience IM development adapter

## What was implemented

Phase 30 closes the Experience diagram at the contract level: Web, IDE, CLI, API,
and IM all terminate at one App Server and one Harness. The adapter does not add an
intelligence path and does not speak Feishu, DingTalk, or WeCom.

- `apps/im-adapter` provides `obsion-im ingest` and `obsion-im serve` (JSON lines)
  on the `development` channel.
- One conversation id maps to one Thread in the `IM` workspace. A second inbound
  message on the same conversation creates a new Turn on that Thread.
- Thread/Turn/Run mutations reuse `ExperienceRuntime` from `obsion-cli`. Evidence,
  Claims, Steps, and wait-for-run stay on REST.
- Vendor channel names are explicit errors. Config cannot store credentials.
- ADR 0009 records that IM is an Experience client, not a bot runtime.

## Architecture decisions

The development channel is an in-process inbox/outbox. That is an explicit mock, not
a fake vendor SDK. A later vendor adapter must implement `ImChannel` and resolve the
sender to a provisioned Principal. Chat text is never an authorization decision.

## Validation

- `uv run pytest --no-cov` — 448 passed, 18 opt-in PostgreSQL tests skipped, including
  `apps/im-adapter/tests` and `test_phase30_experience_im.py`.
- Architecture AST test: IM sources do not import control-plane or vendor IM SDKs.
- Greeting e2e: two development ingest calls on `ops-room` share one Thread, persist
  OBSERVE → … → REFLECT → RESPOND, and do not leak the bearer.
- `uv run ruff check .` — 0 findings.
- `uv run mypy` on control plane, SDKs, CLI, and IM adapter — 0 issues.
- `uv run obsion scan-secrets` — 0 findings.

## Remaining risks

- Live vendor IM apps, public webhooks, and IM-user-to-Principal mapping are
  operator-owned and not implemented.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
