# ADR 0005: Experience CLI is an App Server client

- Status: Accepted
- Date: 2026-08-29

## Context

Obsion Experience includes Web, IDE, CLI, API, and later IM adapters. After Phase 25
the Workbench and SDKs already terminate at one App Server and one Harness. A
repository-native CLI is required so operators and engineers can create Threads,
submit Turns, follow Runs, inspect Evidence and Claims, and decide approvals without
opening a browser. Implementing a second Agent loop in the CLI would violate the
runtime invariant that Web/IDE/CLI/IM must not each own Observe → Understand → Plan →
Execute → Verify → Reflect → Respond.

## Decision

`apps/cli` is a first-class Experience client. It depends on `obsion-sdk` only. Thread,
Turn, Run, Approval, and Artifact metadata mutations use the App Server JSON-RPC
contract with caller-generated `client_request_id` values. Workspace creation, Evidence,
Claims, Steps, and binary Artifact content remain REST, matching the App Server
protocol split. The CLI never imports Harness, Model Gateway, Capability Gateway,
connectors, or persistence. Config files may store `base_url` and `protocol` and are
rejected if they contain credentials; `OBSION_TOKEN` supplies the bearer.

The operator control-plane command remains `obsion` (`serve`, contract validation,
secret scan). The Experience command is `obsion-cli`.

## Consequences

CLI, Workbench, and SDKs share one Principal, one Event Store, and one Policy path.
REST remains available as `--protocol rest` when a WebSocket transport cannot be
injected, for example in process-local tests. IDE and IM adapters can follow the same
client boundary. The CLI cannot execute capabilities, compile SQL, or contact
production systems except through the control plane.
