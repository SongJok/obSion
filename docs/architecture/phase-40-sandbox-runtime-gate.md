# Phase 40 sandbox runtime pin review

## Review question

Is Agent sandbox network policy pinned on the Run plan and enforced at the
Capability Gateway, without claiming container isolation this control plane does
not implement?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- AgentSpec sandbox normalizes `network`, `enabled`, and `mounts`.
- Unknown sandbox keys, `privileged`, host mounts, and `enabled: false` fail closed.
- Harness writes `run.plan.sandbox` from the pinned AgentSpec.
- `network: deny` yields an empty capability set and Gateway `capability_denied`.
- `network: gateway-only` remains the shipped default.
- Security-model text matches the implemented boundary. Docker/gVisor are not added.

## Automated acceptance map

- `test_phase40_sandbox_runtime.py` covers spec normalization, deny/escape rejection,
  planner omission, Gateway deny, unrestricted fail-closed at execution, plan pin on
  a completed Run, and Harness import bans for container runtimes.

## Human review checklist

- Confirm operators do not treat `cpuMillis` / `memoryMb` as cgroup enforcement.
- Confirm `network: deny` is not used on shipped conversational agents.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
