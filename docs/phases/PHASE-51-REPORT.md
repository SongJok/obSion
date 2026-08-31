# PHASE-51-REPORT — Context token budget

## What was implemented

Phase 51 turns Context Builder truncation into an explicit Token Budget Manager.

- `ContextBuilder.pack` decides KEEP / COMPRESS / SUMMARIZE / DROP per segment.
- COMPRESS minifies JSON or collapses whitespace, then truncates to the remaining
  character budget. SUMMARIZE is extractive (identity fields or head/tail) and does
  not call a model.
- Instruction trust and the current user turn are allocated first. They COMPRESS
  rather than DROP while any budget remains. Untrusted evidence stays in
  `<untrusted-data>` wrappers.
- Harness pins the ledger on `runs.context_budget` at first synthesize. Replay copies
  it. Inspector 上下文 shows the ledger. Metric `obsion.context.budget` counts
  actions. Alembic `b50f8e3d0c12`.

## Architecture decisions

Silent prefix slicing was not a Token Budget Manager. An LLM summarize path would be
a second model loop or a fake integration. ADR 0030 keeps decisions deterministic
and auditable on the Run. Vendor IM HTTP remains blocked.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 616 passed, 18 opt-in PostgreSQL tests
  skipped, including `test_context.py` and `test_phase51_context_token_budget.py`.
- Architecture AST: `context.py` has no Model Gateway, HTTP client, eval, or format
  templates.

## Remaining risks

- Character budget still approximates tokens. Provider tokenizers are not invoked.
- Evidence-free CONVERSATION Runs never enter synthesize and keep an empty ledger.
- Vendor IM live HTTP, remote connector processes, and signed `1.0.0` remain
  blocked or operator-owned.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
