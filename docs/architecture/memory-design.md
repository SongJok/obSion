# Governed memory architecture

Obsion memory is a policy-controlled source of context, not an unbounded chat history
or a hidden model-side store. A memory item can influence a Run only after its owner,
classification, retention, policy decision, and human lifecycle state have been made
explicit. The Run then captures the exact authorized material it used as an immutable
snapshot so inspection and replay do not depend on the current mutable view.

## Scope and ownership

The four scopes have exact owner references and decreasing resolution priority:

| Scope | `owner_ref` | Resolution priority | Default retention cap |
| --- | --- | ---: | ---: |
| `TURN` | Turn UUID | 400 | 7 days |
| `SESSION` | Thread UUID | 300 | 30 days |
| `WORKSPACE` | Workspace UUID | 200 | Configured default |
| `USER_PREFERENCE` | Principal UUID | 100 | Configured default |

Turn and session ownership is resolved through their workspace. Workspace memories
require current workspace access. A user-preference memory can be read or written by
that user; cross-user access requires `memory.admin`. Every lookup remains scoped to
the authenticated organization, and unauthorized resources are not disclosed.

## Candidate lifecycle

The persistence pipeline is:

```text
candidate input
  -> owner authorization
  -> redaction and canonicalization
  -> classification floor
  -> retention validation
  -> content-fingerprint deduplication
  -> resource policy decision
  -> candidate persistence
  -> explicit approve or reject
```

The owner workspace classification is a floor: a caller cannot label a memory below
the workspace that contains it. `RESTRICTED` is L3 and is denied by the immutable
generic-memory boundary even if a broad allow policy matches. `CONFIDENTIAL` is L2 and
inherits masking obligations. Every newly persisted candidate records a non-null
`PolicyDecision`; denied writes leave a durable decision and audit record but no
memory content row.

Content is redacted before hashing or persistence. The SHA-256 deduplication key is
computed from canonical JSON within one organization, scope, and owner. Duplicate
submission returns the existing governed item rather than creating divergent copies.
If the new request would raise the classification, it fails with an explicit conflict
instead of silently reusing a lower-classified item; operators must preserve that
classification signal through a governed replacement workflow.

Expiry is mandatory in practice: omitted values receive a bounded default. TURN and
SESSION defaults are additionally limited to seven and thirty days. No caller can
exceed `OBSION_MEMORY_MAX_TTL_DAYS`. Expired candidates or approved items transition
to `EXPIRED` on governed access and are excluded from all Run contexts.

## Run context resolution

Before a new Harness plan is materialized, the runtime resolves approved, unexpired,
policy-decided memories for the current Turn, Thread, Workspace, and principal. It
requires current `memory.read` authority and applies two independent bounds:

- `OBSION_MEMORY_MAX_CONTEXT_ITEMS` limits the number of snapshots;
- `OBSION_MEMORY_MAX_CONTEXT_CHARS` limits their total canonical JSON size.

Higher-priority scopes win when identical content appears more than once. Selected
items become ordered `RunMemorySnapshot` rows containing the content, source memory
ID, policy decision ID, sensitivity, source update time, capture time, and content
fingerprint. These rows are immutable at the PostgreSQL layer.

Memory enters the model only as a labeled untrusted context segment. It cannot alter
system or skill instructions and cannot support a factual Claim without Evidence.
The highest selected memory classification also raises the model-routing and answer
artifact classification floor.

## Inspection and replay

`GET /api/v1/runs/{run_id}/memories` exposes only the snapshots authorized through
the Run's tenant and workspace boundary. The Workbench Memory inspector shows scope,
classification, safe JSON, fingerprint, and capture time so a user can explain which
memory affected an answer.

Deterministic replay copies the source Run's immutable memory snapshots under new
snapshot IDs. It never re-resolves current memory, re-runs policy, or reads newly
edited context. Snapshot fingerprints participate in the replay fingerprint, and
replay events report the copied memory count. A fresh Turn is required when the user
wants current context instead of historical playback.

The source `Memory` row is also database-guarded: status may only move from candidate
to approved/rejected/expired or from approved to expired, while ownership, content,
fingerprint, sensitivity, policy lineage, and creation time cannot be rewritten or
deleted. Expiry and update time remain lifecycle metadata. Retention removal
must therefore be implemented as a separately audited archival/tombstone workflow,
not an ad-hoc delete.

## Operational controls

| Setting | Default | Purpose |
| --- | ---: | --- |
| `OBSION_MEMORY_DEFAULT_TTL_DAYS` | 365 | Default workspace and preference retention |
| `OBSION_MEMORY_MAX_TTL_DAYS` | 3650 | Hard maximum requested retention |
| `OBSION_MEMORY_MAX_CONTEXT_ITEMS` | 40 | Maximum snapshots captured by one Run |
| `OBSION_MEMORY_MAX_CONTEXT_CHARS` | 24000 | Maximum canonical JSON characters per Run |

Changes to these values affect only future candidate defaults and future Run capture.
They never mutate a historical snapshot or the content of an existing memory.
