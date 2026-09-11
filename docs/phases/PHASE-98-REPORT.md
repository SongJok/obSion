# Phase 98 report: local operator access and enterprise connections

Architecture review: [Phase 98 consolidated gate](../architecture/phase-98-architecture-gate.md),
including local password credentials and enterprise connection validation.

## 2026-09-11 connection validation increment

[ADR 0105](../adr/0105-pat-catalog-and-stream-trust.md) adds the governed PAT-wide
inventory REST endpoint, Stream CA initialization and content-free diagnostics,
idempotent capability binding with explicit conflict responses, and strict
compatibility for the real Codeup decimal-string file-size representation.
Existing local edits from ADR 0104 remain part of this validation snapshot.

The designated administrator's password login succeeded. Two actual DingTalk
messages were admitted into persistent Inbox and Harness Runs; the second returned
a cited answer and a vendor SUCCESS/READ receipt. The cloud catalog exhausted two
organizations with 129 unique repositories. All 129 were explicitly registered
with restricted personnel ACLs and passed native repository reads through the
deployed REST/Policy/Gateway; the administrator's cookie session sees all 129.

No schema change is required. An isolated PostgreSQL IM migration round trip
(historical upgrade, downgrade, upgrade to head and drift check) passed, and its
temporary container was removed. Validation counts, file-read results, failure
history and remaining product gates are maintained in the
[M1c validation record](productization-m1c-validation.md).
These are local deployment/test-tenant results, not production promotion or
completion of M1's group, four-intent creation and wider UAT gates.

## What was implemented

Alpha.1 had no way for an administrator to sign in. The only credential
exchange was `POST /auth/session`, which accepts an OIDC access token in
`OIDC` mode and a shared development token in `DEVELOPMENT` mode. A
deployment that federates its people through OIDC therefore had no path
for the local administrator who has to configure that provider in the
first place. Phase 98 closes that gap with a locally enrolled password
credential.

- **Derivation** (`security/passwords.py`): scrypt through
  `hashlib.scrypt`, stored in a self-describing
  `scrypt$n=…,r=…,p=…$<salt>$<key>` encoding so cost parameters can be
  raised later without invalidating enrolled credentials. Verification
  is constant-time (`hmac.compare_digest`). Input is NFKC-normalized
  before derivation so a credential enrolled from one input method
  verifies from another. Requested parameters are memory-bound
  (`128 × r × n ≤ 128 MiB`) so configuration cannot turn login into a
  local denial of service.
- **Policy** (`PasswordPolicy`): length bounds from settings plus a
  refusal of universally breached secrets. The policy is enforced at
  enrollment, never at verification — an already-enrolled credential
  does not become unusable when the policy tightens.
- **Storage** (`persistence/user_credentials.py`): one credential row
  per identity. `UserCredentialStore.enroll` creates or rotates it;
  `verify` resolves the identity by case-insensitive email, loads the
  row `with_for_update()`, and returns a `CredentialVerification`
  carrying `VERIFIED`, `REJECTED`, or `LOCKED`. Derivation is offloaded
  with `asyncio.to_thread` so a deliberately expensive KDF never blocks
  the event loop other requests share. A successful login whose stored
  cost parameters no longer match the configured ones is rehashed
  opportunistically.
- **Enumeration and resource-exhaustion defense**: unknown, ambiguous,
  and unenrolled identities execute the same configured sentinel scrypt
  work as a wrong password. Decoded and configured cost, block-size,
  parallelism, memory, plaintext-size, salt, and derived-key inputs are
  bounded before native derivation, so a corrupted row cannot request
  arbitrary CPU or memory.
- **Authentication** (`security/auth.py::authenticate_password_principal`):
  password login is an additional credential exchange, not a new
  `AuthMode`. It resolves a Principal through the same
  `load_principal_by_id` path as every other exchange, so roles,
  permissions, and organization scope are identical regardless of how
  the session was obtained.
- **HTTP surface** (`POST /auth/password-session`): validates browser
  origin, exchanges the credential, and rotates the same revocable
  HttpOnly session cookie `POST /auth/session` issues. The response is
  the ordinary `AuthSessionView`.
- **Provisioning** (`security/provisioning.py` +
  `obsion provision-user`): a CLI-only path that finds or creates the
  User inside a resolved organization, grants a system role, and
  enrolls the password. It is deliberately never HTTP-exposed, because
  it grants a role with no authenticated caller. The password is never
  accepted as an argument — arguments are readable by any local user
  through the process table — so it is read from piped stdin, then
  `OBSION_PROVISION_PASSWORD`, then an interactive `getpass` prompt.
- **Workbench** (`components/session-gate.tsx`): the login card now has
  an accessible `tablist` with 账户密码 selected first and 访问令牌
  retained. Switching modes clears the entered secret and any stale
  error. A failed attempt drops the secret from component state so it
  is not recoverable from a devtools snapshot.
- **Local environment and Model Gateway**: `.env.example` now covers
  every control-plane `Settings` field plus local operator inputs, and
  the ignored `.env` has the identical key set. Compose loads that file
  optionally into the API only; explicit container-network URLs remain
  authoritative. The local model credential is available only as
  `env://OBSION_AI_API_KEY`, and the configured non-private endpoint is
  bound to `fast`, `reasoning-high`, and `coding-high`, never `private`.

## Architecture decisions

- **Password login is a credential exchange, not an `AuthMode`.** Modes
  select how a deployment federates identity; the local administrator
  credential has to coexist with that choice rather than replace it.
  Both exchanges converge on one Principal loader and one session
  cookie, so authorization has a single implementation.
- **Enumeration resistance is a shared code path, not a convention.**
  Unknown email, enrolled-but-no-credential, and wrong password all
  return `CredentialOutcome.REJECTED` and surface as one
  `invalid_credentials` error. An email that matches users in more than
  one organization is rejected rather than guessed.
- **Lockout accounting lives on the credential row.** The failed-attempt
  counter and `locked_until` are columns on the same row the
  verification locks, so concurrent attempts cannot interleave into a
  lost increment.
- **A rejected attempt still commits.** `create_password_browser_session`
  commits inside its `except ObsionError` block before re-raising.
  Relying on request-scoped rollback would discard the failed-attempt
  counter and make lockout unreachable by simply retrying.
- **Equivalent negative work is part of authentication semantics.**
  Matching HTTP errors are insufficient when one path skips the KDF.
  Identity misses use a non-secret sentinel envelope with the configured
  parameters.

## Migration

`d7f31a9c4b28_add_local_password_credentials` adds `user_credentials`:
organization-scoped, unique per user, holding the encoded secret, the
failed-attempt counter, `locked_until`, `must_change`, and rotation
timestamps. It is additive; no existing column or row is rewritten.

## Validation

- `tests/test_phase98_password_authentication.py` — 28 tests covering
  encoding round-trips, NFKC normalization, memory bounds, policy
  enforcement at enrollment only, breached-secret refusal, rotation,
  opportunistic rehash, indistinguishable rejection plus equivalent KDF
  work, lockout threshold and expiry, cross-organization ambiguity,
  disabled-password-auth refusal, the full `POST /auth/password-session`
  cookie exchange, CLI argument safety, complete environment-key parity,
  API-only optional Compose injection, and Phase/status/release/CI bookkeeping.
- `apps/web/tests/session-gate-interactions.test.tsx` — 7 interaction
  tests: password mode first, trimmed-email exchange, inert submit until
  both fields are supplied, field-agnostic rejection message with the
  password cleared, lockout explained as temporary, token mode still
  functional, and mode switching clearing both error and secret.
- `test_postgres_phase98_user_credential_migration.py` — isolated
  PostgreSQL upgrade/downgrade/re-upgrade with complete column,
  constraint, and index snapshots; the CI migration matrix owns a
  distinct disposable database.
- The destructive migration jobs now upgrade from their historical test
  revision to the current head before Alembic drift detection. The prior
  ordering checked an intentionally old schema and could report later
  migrations as false drift after a successful round trip.
- Error catalog grew to 320 registered codes. The static
  error-producer manifest is checked against `analyze_error_producers()`;
  the gate confirms exact producer/code coverage with no unregistered or
  reserved-code drift.
- `docs/api/openapi.json` regenerated; the diff is purely additive.
- The phase-25 release-hardening gates were fixed to resolve repository
  fixtures from `__file__` instead of the invoking directory. Five of
  them previously passed only when pytest was started from the
  repository root.
- Local Alpha.1 validation completed one real Harness Run through the
  configured Model Gateway endpoint: seven Steps, a successful persisted
  ModelCall, `answer.delta`, and `run.completed`. The non-sending live
  Feishu suite passed four probes with credentials injected only into the
  child process.
- `make check` passed: 1,075 Python tests with 28 documented opt-in
  skips; 188 Web tests in 23 files; 17 Desktop, 12 IDE, and 24
  TypeScript SDK tests; strict formatting/lint/type checking; 38 Golden
  Dataset contracts; zero secret-scan findings; and no Alembic drift.
- A disposable PostgreSQL head schema passed 16 integration tests. Five
  isolated destructive migration databases (audit, Phase 2, Phase 5,
  Phase 79, and Phase 98) each passed their round trip, forward upgrade
  to head, and drift check, then were deleted.
- The optimized Next.js production build passed, the Java SDK passed six
  tests, and the Alpha.1 candidate contract retained all six PENDING
  operator gates with `promotion_eligible=false`.
- Control-plane, migration, and Web images built from the locked sources;
  Compose migrated and restarted to healthy API/Web services. Password
  exchange succeeded against the container API, and a containerized
  Model Gateway retry completed a seven-Step Harness Run with persisted
  input/output token accounting. One earlier provider response failed
  closed as a failed ModelCall while the Run published an evidence-safe
  response, demonstrating rather than hiding provider non-compliance.

## Remaining operator gates

Password login does not reduce any existing operator gate. Production
promotion, staging deploy, UAT, human security sign-off, live OIDC, and
the HIGH/CRITICAL CVE policy remain operator-owned and fail-closed.
`OBSION_PASSWORD_AUTH_ENABLED` lets a deployment refuse local passwords
entirely and rely only on its identity provider.
