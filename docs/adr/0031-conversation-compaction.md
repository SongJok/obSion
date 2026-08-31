# ADR 0031: Conversation compaction is an extractive interface, not a model loop

- Status: Accepted
- Date: 2026-08-29

## Context

Phase 04 required a Conversation summarization interface and Context compaction.
Phase 51 Token Budget can DROP older conversation when the character budget is
exhausted. Capture already truncates by turn/character settings. Neither path left a
reproducible summary of what was omitted.

Calling Model Gateway to summarize thread history would be a second model loop
inside context assembly.

## Decision

`ConversationCompactor` is the interface. The v1 implementation is extractive:
keep the most recent N turns verbatim (default 2) and fold older turns into one
`UNTRUSTED_DATA` segment (`conversation-compact`) containing ordinals and short
previews. The method field is `extractive`. There is no LLM, HTTP, or eval path.

Harness pins the compact ledger on `runs.conversation_compact` at first
synthesize. Replay copies it. Workbench inspector states that this is not a model
summary. Token Budget still decides KEEP/COMPRESS/SUMMARIZE/DROP on the compact
segment like any other untrusted input.

This is not vendor IM HTTP and not conversation-level A/B.

## Consequences

Older thread context remains auditable when it is compacted. A later model-backed
compactor would have to implement the same interface, pin a different `method`,
and go through Model Gateway with policy — it must not be silently substituted
here.
