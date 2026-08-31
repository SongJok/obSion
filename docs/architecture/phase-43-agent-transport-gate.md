# Phase 43 AGENT in-process transport review

## Review question

Can an AGENT capability execute only through the Capability Gateway as an
in-process invocation envelope, produce Evidence, and fail closed on nested
Harness, remote agent URLs, and process spawn—without a second runtime or an
Agent picker in conversation?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `DevelopmentAgentExecutor` is registered for `CapabilityTransport.AGENT`.
- In-process `agent-development` handles `obsion.development.echo`.
- Connector `endpoint`, `allowed_egress`, and harness/spawn/url configuration
  fail closed with `capability_transport_unavailable`.
- AGENT manifests cannot declare nested-runtime shapes.
- Credentials are not copied into the invocation envelope.
- Harness and AutomationWorker do not import the executor. No shipped AgentSpec
  declares it.
- Existing INTERNAL/HTTP/MCP/SDK/GRPC/SQL_PROXY/WORKFLOW transports are unchanged.

## Automated acceptance map

- `test_phase43_agent_transport.py` covers envelope encoding, echo round-trip,
  remote/nested fail-closed, unknown connector/operation, Gateway invocation,
  seeded catalog, AgentSpec exclusion, and AST import bans.
- Registry tests reject nested Harness AGENT manifest shapes.
- Error origin sinks in `error_producer_manifest.py` cover `agent.py`.

## Human review checklist

- Confirm operators do not treat `agent.development.echo` as specialist routing.
- Confirm the composer still has one prompt and no Agent picker.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
