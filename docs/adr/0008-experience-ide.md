# ADR 0008: Experience IDE is an App Server client

- Status: Accepted
- Date: 2026-08-29

## Context

Obsion Experience includes Web, IDE, CLI, API, and later IM adapters. After Phase 26
the Workbench and `obsion-cli` already terminate at one App Server and one Harness.
A repository-native VS Code extension is required so engineers can create Threads,
submit Turns, follow Runs, inspect Evidence and Claims, and decide approvals without
opening a browser or leaving the editor. Implementing a second Agent loop inside
the extension would violate the runtime invariant that Web / IDE / CLI / IM must not
each own Observe → Understand → Plan → Execute → Verify → Reflect → Respond.

## Decision

`apps/ide-extension` is a first-class Experience client. It depends on `@obsion/sdk`
only. Thread, Turn, Run, Approval, and Artifact metadata mutations use the App Server
JSON-RPC contract with caller-generated `client_request_id` values. Workspace
creation, Evidence, Claims, Steps, and Artifact bodies remain REST, matching the App
Server protocol split. Runtime, render, and command modules never import `vscode`;
`extension.ts` is the only host adapter. Settings may store `obsion.baseUrl` and
`obsion.protocol` and are rejected if they contain credentials; the bearer is stored
in VS Code Secret Storage or supplied as `OBSION_TOKEN`.

The default protocol is App Server. `obsion.protocol = rest` exists for environments
without a WebSocket transport, including tests.

## Consequences

Workbench, CLI, IDE, and SDKs share one Principal, one Event Store, and one Policy
path. The extension cannot execute capabilities, compile SQL, or contact production
systems except through the control plane. IM adapters can follow the same client
boundary without copying Harness.
