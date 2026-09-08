# Phase 98 architecture review: local operator access

## Review question

Can Obsion establish the first local administrator and a usable Alpha.1 development
session without adding a second identity/authorization path, exposing plaintext,
weakening OIDC, or sending sensitive traffic to an unapproved model endpoint?

**Status: PASS for repository and local-development scope. Alpha.1 production
promotion remains PENDING on the existing operator-owned gates.**

## Delivery contract

- `POST /api/v1/auth/password-session` is a public credential-exchange endpoint only.
  It validates browser origin, resolves an already provisioned identity, rotates the
  ordinary revocable session, and returns the existing `AuthSessionView`.
- `obsion provision-user` is the sole bootstrap enrollment path. It is local CLI only,
  organization-scoped and idempotent, grants only a declared system role, never accepts
  a password argument, and refuses the weak-password override outside development/test.
- `user_credentials` stores only a salted scrypt envelope and bounded lockout metadata.
  PostgreSQL row locking serializes counter changes; rejected attempts commit.
- Unknown, ambiguous, missing-credential, wrong-password, and malformed-credential
  paths fail closed. Nonexistent identities still perform a bounded sentinel scrypt
  derivation so equal response bodies do not hide a timing oracle.
- Workbench password and access-token tabs converge on the same Principal/session.
  Mode changes and failed exchanges remove secrets from component state.
- `.env.example` covers every `Settings` field plus local Compose/operator variables.
  When an ignored `.env` exists, its key set must match the example exactly. Optional
  Compose loading applies only to the API; internal service URLs override host values.
- Model credentials resolve only inside Model Gateway through
  `env://OBSION_AI_API_KEY`. A local non-private endpoint may serve PUBLIC/INTERNAL
  Runs through logical profiles; the private profile is deliberately unbound.

## Automated acceptance map

- `test_phase98_password_authentication.py` covers derivation, normalization, policy,
  parameter bounds, equivalent unknown-user work, enrollment/rotation, lockout,
  origin checks, browser exchange, CLI argument safety, environment completeness,
  and Compose credential scope.
- `session-gate-interactions.test.tsx` drives the password/token tabs, submission,
  rejection, lockout guidance, and secret clearing through the mounted component.
- `test_postgres_phase98_user_credential_migration.py` runs upgrade, downgrade, and
  re-upgrade in a dedicated disposable PostgreSQL database and verifies the complete
  column/constraint/index contract.
- Static Error/OpenAPI gates cover the new public route and error codes. The full
  repository quality, migration, SDK, and candidate-contract gates remain mandatory.

## Local validation evidence

- The requested administrator is active in the local PostgreSQL organization, holds
  the admin role, and has one unlocked scrypt credential row; no plaintext is stored.
- A local endpoint was created through the audited admin API and bound to `fast`,
  `reasoning-high`, and `coding-high` only. A Harness Run completed through the durable
  Workspace → Thread → Turn → Run → Step → Event model with a successful ModelCall,
  `answer.delta`, and `run.completed`.
- The opt-in, non-sending live Feishu suite passed with process-injected operator
  credentials. DingTalk and WeCom remain untested because no tenant credentials exist.

## Remaining gates

Clean staging deploy/UAT, staging-scoped timed restore, HIGH/CRITICAL registry policy,
artifact signatures, live OIDC/secret manager/read replica, security/data-owner
approval, and maintainer publication authority remain PENDING. No local test or CI
result is allowed to fabricate those approvals or publish with `ci.txt`.
