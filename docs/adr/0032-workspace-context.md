# ADR 0032: Workspace Context is pinned on the Run and split by trust

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt Prompt Context Builder lists Workspace Context as its own layer between
Skill and Memory. Harness synthesized AgentSpec, Skill, user, evidence, memory, and
conversation, but not the Workspace the Thread belongs to. Putting the
operator-authored description into SYSTEM or AGENT instruction text would be prompt
injection by architecture. Re-reading the live Workspace at synthesize would let a
later rename silently change an in-flight or historical Run.

## Decision

Each conversational Turn snapshots Workspace identity and redacted description onto
`runs.workspace_context`. Replay copies the pin. Context Builder emits:

- `workspace-identity` as AGENT (id, name, classification, visibility)
- `workspace-description` as UNTRUSTED_DATA when description is non-empty

Description never occupies SYSTEM trust. Empty pins (pre-migration or unit Runs)
emit no workspace segments. This is not a live workspace lookup and not vendor IM
HTTP.

## Consequences

Workspace metadata is auditable per Run. Changing a Workspace after Turn creation
does not rewrite that Run's context. A later Workspace ACL change still goes through
Policy on capabilities; the pin is context, not authorization.
