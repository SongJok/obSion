# PHASE-54-REPORT — Tool result context

## What was implemented

Phase 54 separates Tool Result from retrieved Evidence in Context Builder.

- `evidence_context_segments` keeps non-TOOL rows on `evidence-bus` and emits
  `EvidenceType.TOOL` on `tool-result`. Both stay `UNTRUSTED_DATA`.
- Harness uses that helper instead of a single JSON dump. Inspector states that
  tool results cannot become SYSTEM or Skill instructions. No schema migration.

## Architecture decisions

Mixing tool payloads into the evidence bus hid the Tool≠Skill boundary. ADR 0033
keeps one Evidence table and two context segments. Vendor IM HTTP remains blocked.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 623 passed, 18 opt-in PostgreSQL tests
  skipped, including `test_phase54_tool_result_context.py`.
- TypeScript SDK: 22 passed.

## Remaining risks

- Other evidence types (LOG, CONFIG) remain on `evidence-bus` by design.
- Vendor IM live HTTP, remote connector processes, and signed `1.0.0` remain
  blocked or operator-owned.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
