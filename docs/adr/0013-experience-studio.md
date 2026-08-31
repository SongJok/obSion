# ADR 0013: Studio is a governed registry workbench

- Status: Accepted
- Date: 2026-08-29

## Context

Obsion Experience already terminates Web, CLI, IDE, IM, and Desktop at one App Server
and one Harness. Goal.txt still calls for Obsion Studio as an Agent / Skill / Workflow
development platform. Administrators could list latest Agent and Skill specs, and
`obsion validate-registry` could check repository YAML, but there was no tenant API
to validate drafts, publish immutable versions, or promote a version into the runtime
without editing files and restarting. Publishing a new `AgentVersion` onto an ACTIVE
definition would have bound the next Turn immediately, because Harness selected the
highest version number.

Conversation UI must keep presenting one assistant. Studio cannot become an Agent
picker.

## Decision

Studio is a Workbench developer surface plus `/api/v1/studio` REST. It reuses
`AgentSpec` / Skill / Workflow manifest validation. It does not implement Harness,
does not call Capability Gateway, and does not store credentials. `registry.read`
authorizes catalog and validate. `registry.write` authorizes publish and promote.
Engineer receives both; Admin retains `*`.

Publish writes an immutable checksummed version with no runtime cutover. Harness and
Turn creation bind `AgentDefinition.active_version` / `SkillDefinition.active_version`
on an ACTIVE definition. Promote updates that mutable pointer and activates a DRAFT
definition. Identical checksums are idempotent. Workflow DAGs can be validated here;
versioned workflow publish stays on the existing automation API.

The Workbench Studio view is a catalog and YAML/JSON editor. It is not shown in the
composer and does not let end users pick specialist Agents.

## Consequences

Draft Agent and Skill revisions can be stored without changing live Runs. Secrets,
DSNs, and provider model IDs still fail closed at validation. Operators promote
explicitly. Conversation routing is unchanged.
