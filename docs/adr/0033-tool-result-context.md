# ADR 0033: Tool results are a separate untrusted Context Builder segment

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt Prompt Context Builder lists Evidence and Tool Result as distinct layers.
Harness previously dumped every Evidence row, including `EvidenceType.TOOL` from
the Capability Gateway, into one `evidence-bus` segment. Tool output is still
untrusted data, but mixing it with retrieved documents hides the Tool≠Skill
boundary and makes Token Budget decisions less precise.

## Decision

`evidence_context_segments` keeps non-TOOL evidence on `evidence-bus` and emits
TOOL rows on a sibling `tool-result` segment. Both are `UNTRUSTED_DATA`. Neither
can become SYSTEM, AGENT, or SKILL. Empty tool lists omit the extra segment so
existing document-only Runs keep a single evidence-bus payload.

This is not a second evidence store and not vendor IM HTTP.

## Consequences

Critic and Claim linkage still use the Evidence table. Context Builder can
compress or drop tool output independently of retrieved documents. A later
per-tool segment would still have to stay untrusted.
