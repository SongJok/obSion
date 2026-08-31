# Phase 51 Context token budget review

## Review question

Does Context Builder record an explicit Keep / Compress / Summarize / Drop decision
for every segment, pin that ledger on the Harness Run, and avoid a second model loop
for summarization?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `ContextBuilder.pack` emits `KEEP`, `COMPRESS`, `SUMMARIZE`, or `DROP` per segment.
- SUMMARIZE is extractive (`method: extractive`). No Model Gateway, HTTP, or eval.
- SYSTEM/AGENT/SKILL and `current-user` are not dropped while budget remains.
- UNTRUSTED_DATA stays wrapped and cannot become a system role.
- First synthesize pins `runs.context_budget`. Replay copies the ledger.
- Workbench inspector renders the ledger. OTel `obsion.context.budget` counts actions.
- Vendor IM HTTP remains unimplemented.

## Automated acceptance map

- `test_context.py` covers isolation, priority truncation, extractive summarize, and
  drop of exhausted history.
- `test_phase51_context_token_budget.py` covers AST bans, Run pin, replay copy, and
  inspector copy.

## Human review checklist

- Confirm operators treat SUMMARIZE as extractive, not an LLM abstract.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
