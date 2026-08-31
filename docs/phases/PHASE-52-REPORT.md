# PHASE-52-REPORT — Conversation compaction

## What was implemented

Phase 52 adds the Conversation summarization interface left from Phase 04 Context
Engineering.

- `obsion.model_gateway.compaction.ConversationCompactor` keeps the last two turns
  verbatim and folds older turns into one extractive `UNTRUSTED_DATA` segment.
- Harness `_conversation_segments` uses that interface before Token Budget.
- The ledger is pinned on `runs.conversation_compact` and copied on replay.
  Alembic `c61a9f4e1d23`. Inspector states this is not a model summary.
- Metric `obsion.conversation.compact` counts compaction outcomes.

## Architecture decisions

LLM thread summaries would be a second model loop or a fake integration. ADR 0031
keeps the interface extractive. Token Budget still applies to the compact segment.
Vendor IM HTTP remains blocked.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 618 passed, 18 opt-in PostgreSQL tests
  skipped, including `test_phase52_conversation_compaction.py`.
- TypeScript SDK: 22 passed.

## Remaining risks

- Capture-time turn/character bounds still omit history before compact runs.
- Preview length is fixed (120 characters). It is not a semantic abstract.
- Vendor IM live HTTP, remote connector processes, and signed `1.0.0` remain
  blocked or operator-owned.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
