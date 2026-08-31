# ADR 0029: Pinned Prompt templates render only schema-bound governed values

- Status: Accepted
- Date: 2026-08-29

## Context

Phase 49 pins PromptVersion on each Run. Templates were injected as raw text.
goal.txt Prompt Context Builder must not treat user utterances as instructions.
`str.format`, eval, or interpolating `question`/`input` into SYSTEM trust would
be prompt injection by architecture.

## Decision

`render_prompt_template` substitutes `{name}` placeholders only when `name` is
declared in the PromptVersion `variables_schema` object properties and provided
as a non-empty redacted string. Unknown placeholders, extra values, nested
braces, secret-like names (`token`, `password`, …), and untrusted names
(`input`, `question`, `user`, …) fail closed.

Harness supplies only governed plan fields currently published as `route`. User
turns, memory, and evidence stay in their own Context Builder segments. Empty
schemas (including `obsion-system-policy` v1) render as identity.

This is not a general templating language and not Jinja.

## Consequences

Pinned prompts cannot grow hidden instruction channels. Authors who need a
variable must publish it on the PromptVersion schema. Vendor IM HTTP remains
unimplemented.
