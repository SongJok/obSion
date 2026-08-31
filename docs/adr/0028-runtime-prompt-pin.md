# ADR 0028: Prompt versions are pinned on the Harness Run

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt requires Prompt / Agent version management with Evaluate and forbids
editing production Prompt text. Phase 48 compared immutable Prompt snapshots but
Harness synthesized with a hardcoded SYSTEM policy and Eval resolved "latest"
prompt names from the Agent spec (usually none). A later Prompt publish would
silently change in-flight or historical Runs if the runtime re-read the table.

PromptDefinition still has no `active_version`. Adding one without a Run pin would
be a fake cutover. Runtime traffic split remains forbidden.

## Decision

Every conversational Turn pins PromptVersion rows onto `runs.prompt_pins` at
creation: the system policy prompt `obsion-system-policy` plus any AgentSpec
`prompts`. The pin stores name, ids, version, and checksum—not the template body.
Replay copies the pin. Context Builder loads templates by pinned `version_id` and
fails closed on checksum mismatch (`prompt_pin_mismatch`). Empty pins (pre-migration
Runs) fall back to the same default system-policy text.

Eval `prompt_pins` on start selects explicit version numbers. Compare reports
`prompt_changed`. Catalog lists Prompt versions for pinning. Prompt rollback in
Studio remains denied: operators publish a replacement snapshot.

This is not a percentage A/B router and not Prompt `active_version`.

## Consequences

New Turns and Evaluation Runs are reproducible against the Prompt snapshot they
pinned. Publishing v2 does not rewrite v1 Runs. Template variable interpolation
against `variables_schema` is a later gate. Vendor IM HTTP and remote connectors
remain unimplemented.
