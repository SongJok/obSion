# Phase 9 Policy Engine and Capability Gateway review

## Review question

The human gate asks whether the structured policy decision and single Capability
Gateway execution boundary are strong enough to become the long-term security base for
Audit, Replay, Evidence, and real connectors. Automated completion does not create a
human signature.

**Status: PENDING — no approver, approval date, or approval conclusion has been
recorded by AI.**

## Decision contract

Each request evaluates:

```text
WHO        user / department / role / permission / attributes
WHAT       capability permission action
RESOURCE   service / environment / table / repository / selector
CONTEXT    environment / time / device / run identity
RISK       descriptor risk and side-effect class
                 ↓
             ALLOW | MASK | ASK | DENY
```

Explicit deny wins. A policy cannot elevate a Principal without the capability's
declared permission. Generic capabilities are read-only through L2; L3–L5 or any
side-effect are denied, and L5 is unconditionally denied. L2's default response is a
masking obligation rather than an instruction to a model.

## Gateway contract

The Gateway resolves only active, tenant-consistent, environment-consistent Registry
versions and enabled bindings. Connector grants are checked before execution. The
execution boundary then performs schema validation, durable approval handling, rate
limiting, short-lived credential brokering, timeout-bounded connector invocation,
output validation, masking, Evidence creation, Event append, and AuditLog write.

An external request cannot impersonate a registered Agent by supplying a display name.
When a Run pins an AgentVersion, the Gateway re-checks its declared capability IDs and
risk budget at execution time. No connector executor is reachable from Harness or an
API route without this boundary.

`ASK` creates a durable approval and transitions the Run/Step to
`WAITING_APPROVAL`; approval is single-use and re-evaluated against the current policy
fingerprint. Distributed rate limiting fails closed outside test environments. Typed
connector errors, timeout, masking, and policy outcomes remain visible through the
stable result, Event, and Audit contracts without exposing credentials.

## Automated acceptance map

- `test_phase9_policy_gateway.py` verifies WHO/WHAT/RESOURCE/CONTEXT/RISK matching,
  no permission elevation, L5 denial, connector grants, AgentSpec capability/risk
  re-check, ALLOW/MASK/ASK/DENY, rate limiting, timeout, typed errors, and zero
  executor calls on blocked paths.
- `test_policy.py`, `test_api_e2e.py`, approval tests, and the existing event/error
  producer gates verify precedence, tenant scoping, masking, approval lifecycle,
  audit/event persistence, and stable machine-readable errors.
- The complete Phase 1–8 contract, identity, App Server, streaming, Workbench, Model
  Gateway, Harness, Registry, OpenAPI, SDK, frontend, PostgreSQL, Compose, and Helm
  gates remain required for this Phase.

## Executed gate evidence

- Phase 9 targeted tests passed: 9 tests.
- Contract quality gates passed after the Gateway error-path and descriptor changes.
- Full Python, SDK, frontend, migration, Compose, and Helm verification is rerun as a
  release gate after this phase's changes; the final counts are recorded in the
  repository change log with the handoff.

## Human review checklist

- Confirm that policies cannot grant a permission absent from the Principal's RBAC
  baseline and that explicit denies always win.
- Confirm that connector grants, AgentSpec pins, environment selectors, rate limits,
  timeout behavior, and approval reuse are sufficient for production threat models.
- Confirm that the Gateway remains the only execution boundary and that no later
  connector implementation may introduce a direct path.
