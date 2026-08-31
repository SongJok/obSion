# Phase 34 Experience Studio review

## Review question

Can developers validate Agent, Skill, and Workflow manifests and publish immutable
Agent/Skill versions from Workbench Studio without a second Harness, without an
Agent picker in conversation, and without unpublished versions binding new Turns?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `/api/v1/studio` validates YAML/JSON with the existing registry contracts.
- `registry.read` is required to list and validate; `registry.write` to publish or
  promote. Engineer receives both permissions.
- Publish creates an immutable checksummed version. Promote updates
  `definition.active_version`. Harness binds that ACTIVE version.
- Specs that contain secrets, DSNs, or provider model IDs return
  `registry_spec_invalid`.
- Workbench Studio is a developer catalog/editor. Composer has no Agent picker.
- Studio application code does not import Harness, Capability Gateway, or Model
  Gateway.
- Workflow publish remains the automation API; Studio may validate the DAG.

## Automated acceptance map

- `test_phase34_experience_studio.py` covers architecture, secret rejection,
  unpromoted publish, Turn pinning, and authorization.
- Python and TypeScript SDKs wrap the Studio routes.
- Contract catalog includes `registry_read_denied`, `registry_spec_invalid`, and
  `registry_write_denied`.

## Human review checklist

- Confirm Engineer/Admin ownership of Studio in the tenant IdP mapping.
- Confirm operators understand promote is the runtime cutover, not publish.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
