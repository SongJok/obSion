# Phase 2 identity, RBAC, and Workspace isolation

This design freezes the identity boundary that every later Harness, Capability,
Evidence, and Policy operation relies on. It is a production architecture increment,
not a temporary login implementation.

## Scope and invariants

The authenticated identity is resolved before a REST service or stateful App Server
method gains authority:

```text
Bearer credential
  -> shared authentication dependency / server.initialize
  -> active provisioned User
  -> organization-scoped Role bindings + Department
  -> immutable Principal snapshot
  -> Workspace ownership / membership
  -> application service
```

The following invariants are mandatory:

1. No `/api/v1` REST route is reachable without the shared authentication dependency.
2. App Server initialization calls the same `authenticate_principal` function and
   records one Principal for the connection.
3. Development authentication requires a configured bearer credential and is rejected
   in production; it never means “assume the local user when no token exists.”
4. `Department` is an organization-owned entity. A user stores `department_id`; a
   department name is never a second identity source of truth.
5. System roles are exactly the stable vocabulary `admin`, `engineer`, `analyst`,
   `operator`, `support`, and `viewer`. Only `admin` owns `*`.
6. A protected repository query includes the Principal organization and, for
   Workspace resources, an owner/member/visibility predicate.
7. Database composite foreign keys reject every cross-organization identity edge.

## Role baseline

Roles are organizational responsibility baselines, not Agent selectors. Workspace
membership still determines which Workspace a principal can see or mutate, and Policy
still evaluates action, resource, environment, classification, risk, and obligations.

| Role | Baseline intent | Special boundary |
| --- | --- | --- |
| `admin` | organization control plane | sole wildcard role |
| `engineer` | governed engineering investigation and evaluation | no identity administration or production write |
| `analyst` | governed data, knowledge, memory, and evidence work | no identity administration or action execution |
| `operator` | governed operations investigation and approval | production remains read-only under V1 boundaries |
| `support` | bounded internal support investigation | no confidential default grant |
| `viewer` | read-only authorized participation | no mutation grant |

Custom roles use explicit lowercase action identifiers. They cannot use a system name
or the wildcard permission. This prevents an innocuous-looking custom role from
becoming an undeclared second administrator role.

## Persistence boundary

Migration `8d3f2a1c7b90` creates `departments`, backfills legacy user department text,
seeds missing system roles for existing organizations, and replaces identity edges
with organization-composite foreign keys:

- `users(organization_id, department_id) -> departments(organization_id, id)`;
- `user_roles(organization_id, user_id) -> users(organization_id, id)`;
- `user_roles(organization_id, role_id) -> roles(organization_id, id)`;
- `workspaces(organization_id, owner_id) -> users(organization_id, id)`;
- all Workspace member, member-user, and creator references carry the same boundary.

The migration refuses pre-existing cross-organization edges rather than normalizing
or hiding a possible security incident. Its downgrade restores the former department
text and foreign-key shape, and a re-upgrade deterministically restores departments.

## Threat model and controls

| Threat | Control | Verification |
| --- | --- | --- |
| Missing-token local login | explicit development bearer comparison; production rejects development auth | unauthenticated and invalid-token API acceptance tests |
| Route forgets its local Principal parameter | shared protected API router dependency | OpenAPI security contract plus unauthenticated Thread creation test |
| Cross-organization role escalation | organization-composite user/role foreign keys | PostgreSQL invalid binding test |
| Cross-organization Workspace owner/member | repository predicates plus composite foreign keys | PostgreSQL invalid owner test and Workspace access acceptance |
| Department spoofing through free text | department entity lookup within caller organization | department/user API test and migration backfill test |
| Custom administrator shadow | reserved system names and wildcard rejection | request-validation acceptance test |
| Resource-existence disclosure | tenant and membership filters before lookup | cross-Workspace not-found assertions |
| Wildcard administrator unexpectedly fails policy condition | wildcard-aware permission matching | Policy Engine regression test |

## Acceptance evidence

- `test_phase2_identity_api.py` proves missing or invalid credentials cannot enter the
  protected API and freezes department and system-role behavior.
- `test_workspace_membership_is_enforced_for_runs_and_writes` proves non-members
  cannot read a Workspace Run, memory, or artifact and read-only members cannot create
  a Turn/Run.
- `test_postgres_phase2_identity_migration.py` proves backfill, role seeding,
  composite constraints, rejected cross-tenant writes, downgrade, and re-upgrade.
- `alembic check` on PostgreSQL proves the migration and ORM metadata have no drift.

## Human role mapping

The source phase requires a human to confirm that these six role baselines can map to
the company's actual responsibilities. This packet does not grant that approval.

Decision: `PENDING`

Reviewer: _unassigned_

Review date: _unassigned_

Notes: _unassigned_
