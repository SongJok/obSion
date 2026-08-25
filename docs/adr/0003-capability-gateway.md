# ADR 0003: Capability Gateway as the mandatory execution boundary

- Status: Accepted
- Date: 2026-08-22

## Context

Agents need access to heterogeneous systems over MCP, HTTP, gRPC, SDKs, and governed SQL. If agents or plugins invoke connectors directly, policy, credentials, masking, evidence, and audit controls can be bypassed.

## Decision

All external work is represented as a versioned Capability and executed only through the Capability Gateway. The gateway validates schemas, evaluates identity/policy/risk, obtains approval where necessary, resolves credentials inside the trust boundary, invokes the connector, validates and masks output, creates evidence, emits events, and writes audit records.

Direct connector imports from agent or skill packages are prohibited by architecture tests.

## Consequences

Every action has uniform safety and observability. The gateway is a critical availability and security component and needs load shedding, idempotency, timeouts, cancellation, and defense-in-depth tests.
