# Phase 6 Model Gateway review

## Review question

The human gate asks whether logical profiles, sensitive-data routing, provider
adaptation, tool-call normalization, and usage/cost accounting are suitable as the
long-term model boundary. Automated completion does not create a human signature.

**Status: PENDING — no approver, approval date, or approval conclusion has been
recorded by AI.**

## Boundary

Agents, Skills, Harness, App Server, SDKs, and the Workbench know only a logical
`ModelProfile`. Provider names, model IDs, base URLs, credentials, pricing, context
windows, and protocol details exist only behind the Model Gateway.

```text
Harness -> profile_id + normalized request -> Model Gateway
                                            -> eligible endpoint
                                            -> provider adapter
                                            -> model_calls accounting
```

The Gateway does not execute a returned tool call. It returns a normalized,
schema-validated `ModelToolCall`; later Harness phases may submit that request only
through the Capability Gateway and Policy boundary.

## Unified completion and tool contract

- `ModelGateway.complete` accepts provider-neutral messages, JSON mode, budgets, and
  zero or more `ModelTool` declarations.
- Tool names are stable identifiers, input schemas must be valid JSON Schema Draft
  2020-12, tool choices must reference a declared tool, and duplicate declarations are
  rejected before any provider request.
- Provider tool arguments must decode to an object and validate against the declared
  schema. Undeclared tools, duplicate call IDs, invalid JSON, schema violations, and an
  ignored required/none/named choice fail closed as `model_unavailable`.
- JSON mode accepts only a JSON object. Provider output remains untrusted and does not
  become a Capability execution or an authorization decision.
- Built-in adapters cover the OpenAI-compatible wire contract used by the configured
  `openai`, `deepseek`, `qwen`, `glm`, and `local` endpoint families. The adapter
  protocol is injectable, so adding another vendor protocol does not change Harness.

## Profile routing and sensitive data

The requested profile and endpoint are organization-scoped and must be enabled. An
endpoint is eligible only when it supports the request classification and all profile
and request capabilities. Profile requirements may additionally constrain provider,
region, minimum context window, and private deployment.

With `OBSION_MODEL_FORCE_PRIVATE_FOR_SENSITIVE=true` (the default),
`CONFIDENTIAL` and `RESTRICTED` inputs replace the requested profile with
`OBSION_MODEL_PRIVATE_PROFILE_NAME`. The effective private profile must exist, be
enabled, require `private=true`, and bind an endpoint whose limits explicitly declare
`private=true`; otherwise the call fails before provider access. Disabling this switch
is an explicit deployment-policy decision, not a prompt or Agent override.

When a profile has `routing_policy.fallback=true`, eligible endpoints are attempted in
priority order. Fallback never changes to a different logical profile and therefore
cannot relax classification, region, tool, context, or private requirements. Without
that flag only the first eligible endpoint is attempted.

## Credentials, egress, and accounting

- Endpoint URLs are validated against the Model Gateway egress allowlist and require
  TLS outside local loopback development/test use.
- Credentials are resolved from `credential_ref` immediately before the request,
  passed only to the adapter, and discarded afterward. They are never stored in a
  profile, model call, Event, browser response, or request fingerprint.
- The persisted fingerprint covers the redacted normalized messages, JSON/tool mode,
  temperature, schemas, and tool choice; raw prompts are not stored in `model_calls`.
- Every provider attempt records effective profile, endpoint, operation, input/output
  tokens, latency, computed cost, and success/failure. A failed primary and successful
  fallback are two honest rows, not one rewritten outcome.
- Input, output, and cost budgets are checked before provider use; provider-reported
  usage determines final accounting.

## Administration contract

Tenant administrators can create/list profiles, create/list endpoints, and bind an
endpoint with an explicit priority. Profile requirements and fallback policy are
typed and secret-free; endpoint configuration accepts only built-in provider protocol
identifiers and separates `credential_ref` from limits and pricing metadata.

The required seeded profiles are `fast`, `reasoning-high`, and `private`. Existing
`coding-high` and `vision` logical profiles remain compatible, but no UI lets an end
user select a vendor model or model ID.

## Automated acceptance map

- `test_phase6_model_gateway.py` proves Profile switching without a Harness change,
  provider-neutral tool calls, schema validation, JSON/cost normalization, private
  override and missing-private fail-closed behavior, per-attempt fallback accounting,
  typed administration, and the absence of provider/model identifiers in Agent,
  registry, and frontend code.
- `test_model_embeddings.py` keeps the existing provider-neutral embedding and budget
  boundary compatible; sensitive routing applies to it through the same profile
  resolver.
- Contract quality gates freeze the administration OpenAPI surface, 248 stable error
  codes, and every new error producer.
- Real PostgreSQL acceptance must prove `model_calls` persists the effective profile,
  endpoint, usage, cost, fingerprint, and fallback outcomes without raw prompt data.
- Ruff, strict mypy, full control-plane tests, Alembic drift/migration, SDK, Web,
  Compose, Helm, and all Phase 1–5 gates remain mandatory.

Executed gate evidence for this Phase:

- the complete control-plane suite passed with 304 tests and 18 opt-in PostgreSQL
  skips;
- a fresh PostgreSQL/pgvector database upgraded through the complete migration chain,
  `alembic check` reported no drift, the Phase 6 fallback/accounting invariant passed,
  and the full non-destructive PostgreSQL integration set passed 15 tests with three
  destructive migration tests intentionally skipped;
- Ruff lint/format, strict mypy, the 92-version Event registry, 248-code error catalog,
  registry/evaluation validation, Python and TypeScript SDK tests, Web lint/typecheck/
  production build, Compose rendering, Helm lint/template, and `git diff --check` all
  passed.

## Human review checklist

- Confirm the three required logical profiles reflect company latency/reasoning/privacy
  policy without exposing model IDs to Agents or users.
- Confirm which endpoints qualify as private and which classifications must force them.
- Confirm provider pricing, region, egress, credential, timeout, and fallback policy.
- Record approver identity, decision, and date only through the real review process.
