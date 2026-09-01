# Phase 93 architecture review: Native Anthropic and Gemini model adapters

## Scope

Close the gap audit's remaining P1 model item: native wire-protocol
adapters for Anthropic (Messages API) and Gemini (generateContent),
behind the existing `ModelProviderAdapter` boundary.

## Model fit

- **The vendor boundary holds.** The Harness still never imports a
  provider API; the new adapters implement the same two-method protocol
  as `OpenAICompatibleAdapter`, and the gateway pipeline (redaction →
  budget estimation → request fingerprint → credential resolution →
  transport → tool validation → cost accounting) is byte-for-byte
  unchanged.
- **Fail-closed parsing.** Every response field is type-checked;
  unknown content-block types, non-object tool arguments, negative
  token counts, and malformed candidates raise `ProviderProtocolError`,
  which the gateway maps into its existing unavailable-model fallback
  path. Nothing the vendor returns is trusted structurally.
- **Policy and egress are untouched.** Endpoint allowlisting
  (`validate_model_endpoint`) and capability routing
  (`chat`/`json_mode`/`tool_call`) apply identically; the admin API's
  provider validation now derives from the same registry the gateway
  serves (`SUPPORTED_PROVIDERS`), so API and runtime cannot drift.
- **Credentials never enter payloads.** Anthropic uses `x-api-key`,
  Gemini uses `x-goog-api-key`, both populated from the resolved
  `credential_ref` exactly like the Bearer path; no secret material is
  serialized into request bodies, events, or telemetry.

## Boundaries held

- No new settings, no schema migration, no endpoint contract change —
  `provider` was already a free-form string validated against the
  registry.
- Live vendor calls remain operator-owned; tests use MockTransport and
  assert the wire contract, not vendor behavior.
- The six PENDING operator gates and all recorded evidence are
  untouched.

## Verification

- 22 unit tests pin both adapters' request shapes, credential headers,
  tool_choice mappings, json_mode mechanisms, response parsing, and
  protocol-error rejection.
- 2 gateway end-to-end tests (MockTransport) prove profile routing,
  credential resolution, system lifting, and token/cost accounting for
  both vendors through the unchanged pipeline.
- Static tests pin the admin validation registry and bookkeeping.
