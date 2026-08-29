# Domain model

## Tenancy and identity

`Organization` owns all protected resources. The Phase 2 principal is an active
`User`; future `Group` and `ServiceAccount` identities must enter through the same
principal contract rather than becoming authentication bypasses. `Department` is a
hierarchical organization-owned entity referenced by users. Role bindings may be
further constrained by workspace, environment, connector, or resource attributes.

The immutable system-role vocabulary is `admin`, `engineer`, `analyst`, `operator`,
`support`, and `viewer`. Custom roles coexist under an organization but cannot shadow
those names. The wildcard permission belongs only to the `admin` system role.

Every persistent record has an organization boundary. Cross-organization joins are
prohibited at the repository layer and tested as a security invariant. Composite
foreign keys additionally prove that a `UserRole`, user department, Workspace owner,
or `WorkspaceMember` cannot point across organizations even if a caller omits an
application-level check.

## Workspace aggregates

- `Workspace`: named container for threads, files, artifacts, evidence, tasks, decisions, and shared memory.
- `Thread`: persistent conversational problem with lifecycle status and parent/fork
  lineage pinned to an exact source Turn; its effective history is the immutable
  inherited prefix followed by branch-local Turns.
- `Turn`: immutable user input plus sanitized attachments and context references.
- `Run`: execution attempt with agent version, model profile, state, budgets, usage, latency, and error classification.
- `RunStep`: ordered/DAG node with dependencies, capability or model action, state, retry policy, and outcome reference.
- `Event`: append-only aggregate fact with correlation and causation.
- `RunFeedback`: one principal's redacted, versioned rating of a terminal Run;
  revisions extend the Run event sequence without affecting verification state.
- `WorkspaceTask`: versioned follow-up item with priority, workspace-member assignee,
  optional source Run, deadline, and a database-enforced lifecycle.
- `WorkspaceDecision`: disposition and supersession header whose current content is
  one immutable, checksummed `WorkspaceDecisionVersion` revision.

## Intelligence aggregates

- `AgentDefinition` and `AgentVersion`: stable name and immutable AgentSpec revisions.
- `SkillDefinition` and `SkillVersion`: stable name and immutable procedure revisions.
- `CapabilityDefinition` and `CapabilityVersion`: organization-owned stable name and
  immutable descriptor revisions. A version pins transport, input/output JSON Schema,
  risk, side-effect class, permission action, timeout, data classification, and the
  Evidence output mapping consumed by Harness and the Capability Gateway.
- `Connector`: configured implementation, credential reference, environment, health, and declared grants.
- `ModelProfile`: routing requirements; `ModelEndpoint`: protected provider configuration.

## Trust aggregates

- `Policy`: versioned rules over principal, agent, action, resource, context, and risk.
- `PolicyDecision`: immutable allow, mask, ask, or deny decision with matched rules.
- `Approval`: durable request, approver constraints, expiration, decision, and resume token hash.
- `AuditLog` (the `AuditRecord` ORM type): append-only actor/action/resource/outcome
  record with policy, approval, risk, latency, and recursively redacted canonical
  dimensions (agent, model profile, capability, resource, and result classification).
- `Evidence`: immutable normalized observation and lineage.
- `Claim`: answer statement with confidence, verification status, and linked evidence.

## Content and artifacts

- `Artifact`: TEXT, TABLE, CHART, SQL, CODE, DIFF, REPORT, DASHBOARD, FILE, or DIAGRAM with storage reference and ACL.
- `Document` and `DocumentVersion`: source identity and immutable ingested revisions.
- `DocumentChunk`: structured content span with inherited ACL and index metadata.
- `Memory`: governed candidate with exact scope owner, sensitivity floor,
  deduplication key, persisted policy decision, approval status, and bounded expiry.
- `RunMemorySnapshot`: immutable, ordered copy of the approved memory context actually
  supplied to one Run, including source/policy lineage and a content fingerprint.
- `RunConversationSnapshot`: immutable, ordered prior-Turn input and selected completed
  answer captured when a new Run is created, with source Thread/Turn/Run/Artifact
  lineage, classification, capture time, and content fingerprint.

## Semantic data model

`Metric`, `Dimension`, `Entity`, `Relation`, `BusinessRule`, `TimeDefinition`, `Synonym`, `DataSource`, `DataTable`, and `DataColumn` form a versioned semantic catalog. Owners and validation status are mandatory for production metrics.

## Evaluation model

`EvaluationDataset` contains immutable `EvaluationCase` revisions. An `EvaluationRun`
pins the dataset fingerprint, agent, resolved skill/capability/prompt versions, model
profile routing metadata, application revision, terminal Run bindings, and gate
policy. Immutable `EvaluationCaseResult` rows store checks, scores, safe observations,
and Evidence fingerprints per case so regressions remain explainable and auditable.

## Automation and action aggregates

- `WorkflowDefinition`, immutable `WorkflowVersion`, and `WorkflowSchedule` define a
  deterministic read-only DAG, trigger policy, accountable owner, and pinned release.
- `AutomationExecution` and `AutomationStepExecution` hold one idempotent occurrence,
  durable lease, child Harness Run/review references, deadlines, and terminal state.
- `NotificationDelivery` is a recipient-scoped, idempotent in-app notification linked
  to an automation execution or governed action.
- `ActionRequest` holds the mutable PR/ticket lifecycle and accountable owner;
  `ActionPlan` is its immutable checksummed execute/rollback contract.
- `ActionApproval` records a purpose-specific, non-self decision for one exact plan;
  `ActionAttempt` pins capability version, connector, policy decision, provider
  idempotency key, safe output, and failure state.

## Identifier and deletion policy

Public identifiers are UUIDv7-compatible strings generated by the application. User
content is soft-deleted with retention metadata; event, audit, policy-decision,
approval, evidence, workflow-version content, and action plans are append-only or
database-guarded against mutation and are removed only by an explicit retention
workflow that leaves a tombstone audit record.

Workspace tasks and decisions follow the same rule. Tasks require exact version
increments and legal state transitions; decision versions are immutable and accepted
decisions can only be replaced through an explicit supersession link.
Run feedback also rejects deletion and identity changes, and every revision increments
its version by exactly one.
Run conversation snapshots are append-only inputs. Retention may remove them only as
part of the owning Run's audited aggregate-retention workflow.
