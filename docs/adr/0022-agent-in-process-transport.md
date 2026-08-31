# ADR 0022: AGENT is an in-process Capability Gateway transport

- Status: Accepted
- Date: 2026-08-29

## Context

`CapabilityTransport.AGENT` existed in the enum and registry with no executor.
Invoking an agent capability returned `capability_transport_unavailable`. goal.txt
lists Agent as a capability kind beside Tool, MCP, API, SDK, and Workflow. Starting
a nested Harness loop, spawning a sidecar, or calling a remote agent HTTP API would
create a second runtime.

## Decision

Register `DevelopmentAgentExecutor` for `CapabilityTransport.AGENT`. It encodes
`{agent, operation, input}` and dispatches to in-process handlers keyed by
`connector_type`. The development adapter exposes `obsion.development.echo` on
connector type `agent-development`.

Connector endpoint, non-empty egress, and configuration keys that imply a nested
runtime (`harness`, `spawn`, `sidecar`, `child_run`, `url`, `host`, and similar)
fail closed with `capability_transport_unavailable`. Registry manifests with
`transport: AGENT` are rejected if they declare those shapes. Connector credentials
are not copied into the invocation envelope. Harness and `AutomationWorker` do not
import the executor. No AgentSpec declares the development echo capability.

This is not a second Harness. Conversational specialist routing stays on
Understanding and AgentRouter. Users still cannot pick an Agent in the composer.

## Consequences

Operators can bind a versioned AGENT capability through the same Gateway path as
other transports. Nested child Runs and remote agent meshes remain unimplemented
until a later ADR with an explicit recursion budget. Vendor IM HTTP remains
unimplemented.
