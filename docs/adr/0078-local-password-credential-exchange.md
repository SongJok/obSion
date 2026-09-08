# ADR 0078: Local passwords are a bounded credential exchange

- Status: accepted
- Date: 2026-09-04
- Phase: 98

## Context

Alpha.1 supported OIDC bearer exchange and a shared development bearer, but it
did not provide a durable way to sign in as the first local administrator. An
OIDC-only answer is circular when that administrator must configure the identity
provider through Obsion. Adding a second identity, authorization, or Agent path
would violate the one-control-plane architecture.

Password verification also creates two security-specific risks: a fast rejection
for an unknown email becomes a timing-based identity oracle, and attacker-controlled
or corrupted self-describing KDF parameters can turn verification into resource
exhaustion.

## Decision

1. A local password is an additional credential exchange, not an `AuthMode`.
   Successful verification resolves the existing organization-scoped `Principal`
   and rotates the same revocable HttpOnly browser session used by bearer exchange.
2. Credentials are enrolled only through the local operator CLI. No unauthenticated
   HTTP route can create users, grant roles, or set passwords. Plaintext is read from
   stdin, a one-use process environment value, or an interactive hidden prompt; it is
   never accepted as a process argument.
3. PostgreSQL stores one salted, self-describing scrypt derivation per User. The
   encoded value, failure counter, lock deadline, and rotation timestamps share one
   organization-scoped row. Verification locks that row before changing counters.
4. Unknown, ambiguous, and unenrolled identities perform the same configured scrypt
   work as a wrong password before returning the identical error. Stored scrypt cost,
   block size, parallelism, memory, plaintext, salt, and derived-key shapes are all
   bounded before native derivation.
5. Rejected attempts commit their counter update. Successful attempts clear the
   counter and opportunistically rehash when configured parameters change.
6. The deliberately weak local password override is accepted only in development or
   test. Staging and production cannot use that override. Deployments may disable the
   password exchange entirely.
7. Local Compose may load an optional ignored `.env` into the API process so Gateway
   credential references resolve there. The migration container does not receive that
   file. Container-network database, Redis, object-store, and origin values continue
   to override host-side values. Production uses Helm plus a secret manager.
8. A locally configured non-private model endpoint may bind `fast`,
   `reasoning-high`, and `coding-high`; it must not bind `private`. CONFIDENTIAL and
   RESTRICTED traffic therefore continues to fail closed unless an independently
   approved private endpoint exists.

## Consequences

- Password and bearer callers converge before authorization; Policy, Workspace ACL,
  Audit, Capability Gateway, Harness, and Event behavior do not branch by credential.
- The first administrator can be provisioned without inventing a bootstrap web API.
- A lost local password is rotated by an operator re-running the idempotent CLI. There
  is no email-reset channel or recoverable plaintext.
- The migration is additive and has an isolated upgrade/downgrade/re-upgrade CI job.
- Local AI credentials remain process-only through `env://` references; model IDs and
  endpoints stay in Model Gateway configuration, never in AgentSpec.
- This decision does not satisfy staging, UAT, OIDC, secret-manager, registry
  signature/CVE, disaster-recovery, publication, or human approval gates.
