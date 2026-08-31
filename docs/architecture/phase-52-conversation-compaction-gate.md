# Phase 52 Conversation compaction review

## Review question

Is thread history compacted through an explicit extractive interface that keeps
recent turns verbatim, isolates older turns as untrusted data, and never calls a
model to summarize?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `ConversationCompactor.compact` keeps the last N turns and extractively previews
  older turns.
- The compact segment is `UNTRUSTED_DATA` / `conversation-compact`.
- First synthesize pins `runs.conversation_compact`. Replay copies the ledger.
- Inspector discloses extractive (non-model) compaction.
- No Model Gateway, HTTP client, eval, or format templates in `compaction.py`.
- Vendor IM HTTP remains unimplemented.

## Automated acceptance map

- `test_phase52_conversation_compaction.py` covers extractive keep/summarize and
  AST bans.
- `test_conversation_context.py` still requires two recent turns verbatim.

## Human review checklist

- Confirm operators do not treat the compact preview as an LLM abstract.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
