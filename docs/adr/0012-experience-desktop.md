# ADR 0012: Experience Desktop is an App Server client

- Status: Accepted
- Date: 2026-08-29

## Context

Obsion Experience includes Web, IDE, CLI, API, IM adapters, and Desktop. After
Phase 32 those clients already terminate at one App Server and one Harness. A
repository-native desktop shell is required so operators can create Threads, submit
Turns, follow Runs, inspect Evidence and Claims, and decide approvals in a dedicated
window without opening a browser or VS Code. Implementing a second Agent loop inside
the desktop process would violate the runtime invariant that Experience clients must
not each own Observe → Understand → Plan → Execute → Verify → Reflect → Respond.

Electron is a window host, not a runtime. Shipping Electron as a required npm
dependency would download a browser binary into every CI job. The desktop client must
still be testable without that binary.

## Decision

`apps/desktop` (`@obsion/desktop`, command `obsion-desktop`) is a first-class
Experience client. It depends on `@obsion/sdk` only. Thread, Turn, Run, Approval, and
Artifact metadata mutations use the App Server JSON-RPC contract with caller-generated
`client_request_id` values. Workspace creation, Evidence, Claims, Steps, and Artifact
bodies remain REST, matching the App Server protocol split.

Runtime, session, render, secrets, and the loopback window server never import
Electron. `electron-main.ts` is the only window-host adapter and may only load
`http://127.0.0.1`. The loopback UI binds `127.0.0.1` only. Config JSON may store
`baseUrl`/`base_url` and `protocol` and is rejected if it contains credentials. The
bearer is stored in `~/.config/obsion/desktop.secret` with mode `0600`, or supplied as
`OBSION_TOKEN`.

The default protocol is App Server. `protocol = rest` exists for environments without
a WebSocket transport, including tests. `obsion-desktop ask` is the headless path;
`obsion-desktop serve` is the windowed path.

## Consequences

Web, CLI, IDE, IM, and Desktop share one Principal, one Event Store, and one Policy
path. The desktop client cannot execute capabilities, compile SQL, or contact
production systems except through the control plane. Operators who want a native
window install Electron beside this package; CI proves the Experience contract
without that binary.
