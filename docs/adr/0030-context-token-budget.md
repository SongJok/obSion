# ADR 0030: Context Token Budget is an explicit Keep/Compress/Summarize/Drop ledger

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt Prompt Context Builder requires a Token Budget Manager that decides Keep,
Compress, Summarize, or Drop. Phase 04 already truncated Context Builder segments by
priority. That was silent prefix slicing: no ledger, no summarize, and no distinction
between instruction trust and untrusted evidence.

Calling Model Gateway to summarize overflow would be a second model loop inside
context assembly. Claiming LLM summarization without that call would be a fake
integration.

## Decision

`ContextBuilder.pack` is the Token Budget Manager. Each segment receives exactly one
decision:

- KEEP when the original text fits the remaining character budget.
- COMPRESS when instruction or current-user overflow (whitespace/JSON minify, then
  truncate). Historical USER/ASSISTANT overflow also COMPRESS while budget remains.
- SUMMARIZE for UNTRUSTED_DATA overflow when at least 24 characters remain. The
  method is extractive (`id`/`type`/`source`/`resource`/`scope`, or head/tail). It
  does not call a model.
- DROP when remaining budget is zero. SYSTEM/AGENT/SKILL/current-user are allocated
  first by existing priority; they are not dropped while budget remains.

The serialized ledger is pinned on `runs.context_budget` at first synthesize and
copied on replay. Empty ledgers (pre-migration or evidence-free conversation) stay
`{}`. OTel `obsion.context.budget` counts decisions. Workbench inspector shows the
ledger. Budget still applies to segment content, not wrapper prefixes, so untrusted
isolation wrappers cannot steal the instruction allocation.

This is not conversation-level LLM compaction and not vendor IM HTTP.

## Consequences

Overflow is auditable per Run. Untrusted evidence cannot become SYSTEM text when
summarized. Replay preserves the original ledger. A later conversation-summary
interface may consume this ledger; it must not invent a second Harness or a silent
model summarize.
