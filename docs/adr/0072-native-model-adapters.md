# ADR 0072: Native Anthropic and Gemini model adapters

## Status

Accepted (Phase 93, 0.93.0-dev)

## Context

The Model Gateway has routed every completion through a single
`OpenAICompatibleAdapter` since Phase 6, registered for openai,
openai-compatible, deepseek, qwen, glm, and local endpoints. The Alpha.1
gap audit flagged the remaining P1 model gap: no native adapters for
the two major non-OpenAI wire protocols — Anthropic's Messages API and
Gemini's generateContent API. Operators running Claude or Gemini models
had to front them with an OpenAI-compatible proxy, which loses native
tool-use semantics and is one more moving part in a regulated path.

Constraints: the vendor protocol boundary is explicit — the Harness
never imports provider APIs, so new protocols must implement the
existing `ModelProviderAdapter` protocol (build request / parse
response) without touching the Harness, the gateway pipeline
(redaction, budgets, fingerprints, cost accounting, tool validation),
or the endpoint schema. Credentials keep flowing through
`credential_ref` resolution and must never enter payloads. All six
operator gates stay untouched; live vendor calls remain operator-owned.

## Decision

1. **Two new adapters behind the existing protocol.**
   `AnthropicAdapter` speaks `v1/messages` (x-api-key,
   anthropic-version 2023-06-01, content blocks, tool_use); `GeminiAdapter`
   speaks `v1beta/models/{model}:generateContent` (x-goog-api-key,
   contents/parts, functionDeclarations, functionCallingConfig). Both
   are registered in `builtin_provider_adapters()` under the provider
   names `anthropic` and `gemini`.
2. **System content is lifted, conversation shape is normalized.**
   Both vendors hoist system instructions out of the message list; a
   shared `_split_system_messages` helper joins system-role messages
   and rejects non-string content or unknown roles with
   `ProviderProtocolError`, so malformed gateway input fails before any
   HTTP call.
3. **tool_choice maps to native semantics, losslessly.**
   auto/required/none/named map to Anthropic auto/any/none/tool and
   Gemini AUTO/ANY/NONE/ANY+allowedFunctionNames. The gateway's
   existing `validate_tool_calls` then verifies the provider honored
   the choice — undeclared tools, schema-violating arguments, and
   ignored selections remain fail-closed.
4. **json_mode is honest about each vendor's mechanism.** Gemini gets
   the native `responseMimeType: application/json`; Anthropic has no
   equivalent, so the adapter appends an explicit JSON-only instruction
   to the system prompt (the gateway's JSON validation stays the
   enforcement point either way).
5. **Gemini call ids are synthesized.** The generateContent response
   carries no tool-call ids; the adapter assigns `call_{ordinal}` so
   the gateway's uniqueness check and downstream correlation keep
   working without inventing provider data.
6. **Admin validation follows the registry.** A single
   `SUPPORTED_PROVIDERS` frozenset (OpenAI-compatible ∪ anthropic ∪
   gemini) replaces the OpenAI-only constant in admin endpoint/profile
   validation, so the API can never accept a provider the gateway
   cannot serve.

## Consequences

- Claude and Gemini endpoints are first-class: profiles route to them
  through the identical redaction → budget → fingerprint → transport →
  validation → cost pipeline, proven by MockTransport end-to-end tests
  for both vendors.
- Live vendor verification (real Anthropic/Gemini credentials) remains
  operator-owned, exactly like the Feishu live ladder; this phase ships
  the protocol implementation and its fail-closed parsing, not a
  vendor attestation.
- No schema, settings, Harness, or client change; rollback is
  reverting the phase commits.
