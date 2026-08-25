# Action Agent architecture

## Purpose and release boundary

Phase 7 adds a governed change-execution plane to Obsion. The Action Agent is not a
free-form autonomous agent and it does not receive infrastructure credentials. It
turns a user-authored, schema-validated change request into an immutable execution
plan, obtains an independent human decision, and invokes one pinned, registered
capability through a dedicated action gateway. Every transition is durable,
idempotent, observable, and reversible through a separately approved compensating
action.

The first release opens only `GENERATE_PR` and `CREATE_TICKET`, and only in
`development` or `staging`. Their capabilities must be `L3` and
`IDEMPOTENT_WRITE`. The following remain hard-denied by the server regardless of
role, policy, connector, or client behavior:

- every action whose target environment is `production`;
- `MODIFY_CONFIG`, `RESTART_SERVICE`, and `DEPLOY` execution;
- destructive capabilities, production database writes, automatic remediation,
  and action requests without a rollback capability.

This is gradual enablement rather than a simulated action layer. An action can be
submitted only when a real, active capability version and environment-specific
connector binding pass preflight. If no provider has been configured, preflight
fails closed and no fake success is produced.

## Domain model

- `ActionRequest` is mutable lifecycle state and accountable ownership. It contains
  the requested action type, non-production environment, target, parameters,
  idempotency key, deadline, lease, safe result, and stable failure information.
- `ActionPlan` is the immutable, checksummed snapshot produced by preflight. It pins
  exact execute and rollback capability versions, connectors, target, and sanitized
  payloads. PostgreSQL rejects updates and deletes at the database boundary.
- `ActionApproval` is independent from Harness read approvals. It records purpose
  (`EXECUTE` or `ROLLBACK`), the exact plan checksum, expiry, approver constraints,
  decision actor, reason, and time. Requesters cannot approve their own action.
- `ActionAttempt` is one idempotent provider invocation. Execute and rollback use
  different stable idempotency keys. Attempts persist the pinned capability and
  connector, policy decision, timestamps, safe output, and stable error code.

All records are organization-scoped and workspace access is rechecked for every API
operation. The action owner is reloaded before execution and must remain active,
retain workspace write access, and retain both `action.execute` and the pinned
capability permission.

## Lifecycle

```text
DRAFT -> PREFLIGHT_FAILED
      -> WAITING_APPROVAL -> REJECTED / EXPIRED / CANCELLED
                          -> APPROVED -> EXECUTING -> COMPLETED / FAILED
COMPLETED or FAILED -> WAITING_ROLLBACK_APPROVAL
                    -> ROLLBACK_APPROVED -> ROLLING_BACK
                                         -> ROLLED_BACK / ROLLBACK_FAILED
```

Preflight can be repeated while a request is still a draft or has failed preflight.
Once a plan is created, the target and parameters are frozen. A changed request must
be created with a new idempotency key and receive a new approval.

Execution approval never implicitly authorizes a later rollback. Rollback creates a
new approval tied to the same immutable plan. This makes the compensating operation
visible and prevents a stale execution decision from becoming an open-ended write
grant.

## Preflight and policy

Preflight performs deterministic checks before a reviewer is asked to decide:

1. organization, workspace write access, ownership, and action permissions;
2. action-type release boundary and non-production environment;
3. action-specific target and parameter schema, size limits, and secret-field denial;
4. active execute and rollback capability definitions and exact versions;
5. `L3` + `IDEMPOTENT_WRITE` side-effect contract for both capabilities;
6. enabled environment-specific bindings, active connectors, matching resource
   selectors, declared grants, egress allowlists, and transport availability;
7. plan checksum, deadline, and provider idempotency contract.

The existing Capability Gateway remains read-only and cannot be opened by passing a
different agent name. Writes use `ActionGateway`, which shares the same versioned
registry, credential broker, connector executors, schema validation, policy decision
store, rate limiting, redaction, event store, and audit vocabulary. Its policy entry
point is not exposed as a general capability invocation API and requires an approved,
unexpired `ActionApproval` whose checksum equals the pinned plan.

Organization policies may still deny an action. Approval cannot override a `DENY`,
the release boundary, missing permission, deactivated connector, changed capability,
or invalid plan.

## Provider contract and idempotency

Action providers are registered capabilities. The V1 HTTP provider receives a JSON
envelope with action type, purpose, target, pinned parameters, action request ID, and
plan checksum. The stable attempt key is sent in the `Idempotency-Key` header;
`X-Obsion-Action-ID` and `X-Obsion-Action-Purpose` provide explicit correlation.
Credentials are resolved only inside the trust boundary and are never persisted in an
action, event, audit record, or model context.

For example, a PR execution body is:

```json
{
  "action_type": "GENERATE_PR",
  "purpose": "EXECUTE",
  "target": {"repository": "organization/repository"},
  "parameters": {
    "title": "fix: bounded change",
    "head": "fix/bounded-change",
    "base": "main",
    "body": "Reviewed change summary"
  },
  "obsion": {
    "action_request_id": "01900000-0000-7000-8000-000000000001",
    "plan_checksum_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  }
}
```

The registered capability version owns an exact Draft 2020-12 schema. Standard create
responses contain only `external_id` and `url`; compensating responses contain only
`external_id` and `state: "closed"`. Unknown fields, invalid URLs, oversized bodies,
non-object JSON, redirects, and schema mismatches fail without persisting raw output.
Changing a provider contract creates a new capability version and requires an explicit
binding; it does not mutate an existing plan.

Workers use row locking with `SKIP LOCKED` and renewable leases. If a worker crashes
after the provider commits but before Obsion records the response, the replacement
worker repeats the same attempt with the same idempotency key. Providers must return
the original result for duplicate keys. Non-idempotent write capabilities are not
eligible for V1 binding.

The rollback envelope includes the original safe provider output and the pinned
rollback parameters. It is itself idempotent, separately authorized, and audited.

## Audit and failure model

Creation, preflight, plan sealing, approval request/decision/expiry, execution claim,
provider completion/failure, cancellation, rollback request/decision/completion, and
notification delivery emit append-only events and audit records. Provider output is
validated and redacted before persistence. Raw connector exceptions and credentials
never enter client responses.

A provider failure produces a durable `FAILED` action and offers rollback because an
external system may have committed before its response was lost. A rollback failure
is terminal and prominently visible for human remediation; Obsion does not recurse
into autonomous repair.

## Acceptance gates

- Generic capability invocation still denies every side effect.
- Production actions and the three deferred action types are denied server-side.
- An action cannot execute without a non-self, unexpired approval for its exact plan.
- Deactivating a connector or revoking owner permission after approval fails closed.
- Reclaiming an expired lease reuses the same provider idempotency key.
- Execute and rollback both create policy decisions, events, attempts, audit records,
  and user-visible notifications.
- PostgreSQL rejects mutation or deletion of an action plan.
- Cross-organization and cross-workspace access remains impossible.
