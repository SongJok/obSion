# Phase 41 gRPC in-process transport review

## Review question

Can a gRPC capability execute only through the Capability Gateway as an in-process
unary envelope, produce Evidence, and fail closed on remote hosts, TLS channels,
protobuf/grpcio, and non-empty egress—without a second runtime or a Java control
plane?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `DevelopmentGrpcExecutor` is registered for `CapabilityTransport.GRPC`.
- In-process `grpc-development` handles `obsion.development.Echo/Ping`.
- Connector `endpoint`, `allowed_egress`, and host/port/tls/channel configuration
  fail closed with `capability_transport_unavailable`.
- GRPC manifests cannot declare remote channel shapes.
- Credentials are not copied into the invocation envelope.
- Harness does not import the gRPC executor. No shipped AgentSpec declares it.
- Existing INTERNAL/HTTP/MCP/SDK/SQL_PROXY transports are unchanged.

## Automated acceptance map

- `test_phase41_grpc_transport.py` covers envelope encoding, echo round-trip, remote
  fail-closed, unknown connector/method, Gateway invocation, seeded catalog, AgentSpec
  exclusion, and AST import bans.
- Registry tests reject host/port GRPC manifest shapes.
- Error origin sinks in `error_producer_manifest.py` cover `grpc.py`.

## Human review checklist

- Confirm operators do not treat `grpc.development.echo` as a production stub.
- Confirm `grpcio` / remote HTTP/2 remain absent.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
