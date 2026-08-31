# ADR 0019: Sandbox is pinned on the Run and enforced at the Gateway

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt requires sandbox network DEFAULT DENY, with Capability Gateway, Model
Gateway, and Artifact Store as the only approved paths, and declared CPU, memory,
disk, process, filesystem, and timeout bounds. AgentSpec already rejected
`sandbox.network: unrestricted` at registry load (Phase 25), but Harness ignored
sandbox at runtime and `docs/security/security-model.md` described OS isolation
that this control plane does not implement. Docker/gVisor wrappers would fake a
boundary.

## Decision

Normalize AgentSpec sandbox at parse time:

- `network` is `deny` or `gateway-only` (default `gateway-only`).
- `enabled` must be true.
- `mounts` may only name `/workspace`, `/repo`, `/artifacts`, and `/tmp`.
- Optional `cpuMillis`, `memoryMb`, `diskMb`, and `processLimit` are stored as
  declarations.
- Privileged, Docker, hostNetwork, and unknown sandbox keys fail closed.

Harness pins the normalized sandbox on `run.plan["sandbox"]`. Replay copies the
AgentVersion and re-pins from that spec. When `network` is `deny`, the planner
exposes no capabilities and the Capability Gateway treats the pinned AgentSpec as
not capability-allowed, returning `capability_denied` before the executor.
`gateway-only` keeps the existing path: capabilities execute only through the
Gateway. Model Gateway and Artifact Store stay on their own control-plane paths.

This control plane does not start containers, apply cgroups, or mount host paths.
Operating-system isolation remains operator-owned.

## Consequences

Operators can inspect the pinned network mode on a Run. `network: deny` is a real
runtime deny, not documentation. CPU and memory numbers in AgentSpec are not an
OS guarantee. Vendor IM HTTP remains unimplemented. gRPC remains uninstalled.
