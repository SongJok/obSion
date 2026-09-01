# Phase 93 report: Native Anthropic and Gemini model adapters

## What was implemented

The gap audit's remaining P1 model item is closed — Claude and Gemini
endpoints are now first-class citizens of the Model Gateway:

- **AnthropicAdapter**: the Messages API wire contract — `v1/messages`,
  `x-api-key` + `anthropic-version` headers, system content lifted out
  of the message list, `input_schema` tool declarations, tool_choice
  mapped to auto/any/none/tool, content-block parsing (text + tool_use)
  with fail-closed protocol validation, and a JSON-only system
  instruction for json_mode (the gateway's JSON validation remains the
  enforcement point).
- **GeminiAdapter**: the generateContent wire contract —
  `v1beta/models/{model}:generateContent`, `x-goog-api-key`,
  systemInstruction, user/model contents with text parts,
  functionDeclarations, functionCallingConfig modes
  (AUTO/ANY/NONE/allowedFunctionNames), `responseMimeType:
  application/json` for json_mode, and synthesized `call_{ordinal}` ids
  for function calls (Gemini assigns none).
- **Registry**: `SUPPORTED_PROVIDERS` (OpenAI-compatible ∪ anthropic ∪
  gemini) is now the single source for both `builtin_provider_adapters()`
  and admin endpoint/profile validation.

## Architecture decisions

ADR 0072 records the six decisions: adapters behind the existing
protocol, shared system-lifting helper, lossless tool_choice mapping,
honest per-vendor json_mode, synthesized Gemini call ids, and
registry-driven admin validation.

## Migration

None. No schema, settings, Harness, or client change; endpoints opt in
by setting `provider: anthropic` or `provider: gemini`. Rollback is
reverting the phase commits.

## Validation

- `test_phase93_native_model_adapters.py` — 22 adapter unit tests
  (request shapes, headers, tool_choice matrices, parse success and
  protocol-error rejection for both vendors) plus 2 MockTransport
  gateway end-to-end tests proving profile routing, credential
  resolution, and token accounting through the unchanged pipeline.
- `make check` (pytest across the control plane, all node suites,
  eslint, tsc, alembic) and `make test-java` pass.
- `make validate-release-candidate-contract`: 2 live ledgers, 2 drill
  ladders, 16 checks, 6 PENDING operator gates unchanged.

## Deferred findings still open

Automation Web authoring depth, Code Intelligence cross-language
precision, post-conclusion context actions, the operations analytics
loop, full admin CRUD, and a schema-driven chart renderer remain
candidates. Live Anthropic/Gemini verification with real credentials is
operator-owned, matching the Feishu live-ladder precedent.

## Remaining operator gates

All six Alpha.1 candidate gates remain PENDING (staging deployment,
staging-scoped timed DR drill, registry HIGH CVE policy and signed
promotion, live OIDC/secret-manager/replicas, security and data-owner
sign-off, signed publication). This phase adds vendor protocol support
and does not advance promotion.
