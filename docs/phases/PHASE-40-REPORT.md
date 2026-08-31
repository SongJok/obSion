# PHASE-40-REPORT — Sandbox runtime pin

## What was implemented

Phase 40 makes Agent sandbox a first-class Run pin and a Gateway enforcement
point. It does not start a container runtime.

- `AgentSpec` normalizes `sandbox.network` (`deny` | `gateway-only`), requires
  `enabled: true`, and allows mounts only under `/workspace`, `/repo`,
  `/artifacts`, and `/tmp`. Optional CPU/memory/disk/process fields are stored as
  declarations. Privileged and Docker-shaped keys fail closed.
- Harness writes the normalized sandbox onto `run.plan.sandbox`.
- `network: deny` removes capabilities from the planner and fails
  `_agent_capability_allowed` at the Gateway (`capability_denied`).
- Workbench inspector shows the pinned network mode. Governance copy states
  sandbox traffic is Gateway-only.
- ADR 0019 records the honest boundary. No schema migration.

## Architecture decisions

Network policy is enforced where capabilities already execute: the Capability
Gateway. Model Gateway and Artifact Store stay on existing control-plane paths.
OS isolation is operator-owned and is not claimed. Vendor IM HTTP and gRPC remain
unimplemented.

## Validation

- `uv run pytest --no-cov` — 533 passed, 18 opt-in PostgreSQL tests skipped,
  including `test_phase40_sandbox_runtime.py`. `uv run obsion scan-secrets` —
  0 findings.
- Workbench runtime inspector shows `沙箱 gateway-only` on the pinned Run plan
  (summary chip and 轨迹 metrics). Governance catalog notes sandbox traffic is
  Gateway-only. Composer still has one prompt and no Agent picker.

## Remaining risks

- Declared CPU/memory/disk/process limits are not applied by this process.
- Public IM webhook hosting, WeCom AES decrypt, and vendor HTTP POST still require
  a real tenant application.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
- Signed `1.0.0` remains operator-owned.
