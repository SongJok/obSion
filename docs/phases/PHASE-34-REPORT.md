# PHASE-34-REPORT — Experience Studio

## What was implemented

Phase 34 adds Obsion Studio as a governed Agent / Skill development workbench on the
existing control plane. It does not implement a second Harness and does not present
an Agent picker in conversation.

- `GET /api/v1/studio/catalog`, `POST /api/v1/studio/validate`, publish Agent/Skill
  versions, and `POST /api/v1/studio/promote`.
- Validation reuses registry YAML contracts, including Workflow DAG checks.
- Publish writes an immutable checksummed version. Promote updates
  `definition.active_version`; Harness binds that version of an ACTIVE definition.
- Workbench **Studio 开发台** edits YAML/JSON, validates, publishes, and promotes.
- Engineer gains `registry.read` and `registry.write`. Secrets in specs fail closed.
- Skill/Agent definitions gain `active_version` (Alembic `f34b8d1e2c90`).
- ADR 0013 records that Studio is a registry workbench, not a runtime.

## Architecture decisions

Unpublished versions must not change live Turns. Promote is explicit. Workflow
version publish stays on the automation API so Studio does not duplicate scheduler
and event contracts.

## Validation

- `uv run pytest --no-cov` — 479 passed, 18 opt-in PostgreSQL tests skipped,
  including `test_phase34_experience_studio.py`.
- Workbench at `http://localhost:3000`: sidebar **Studio 开发台** is between 数据目录
  and 治理控制台. Composer has one prompt and no Agent picker.
- Studio catalog loaded 8 builtin Agents as ACTIVE 运行中. Validate of a YAML spec
  containing `secret: hunter2` returned HTTP 422 `registry_spec_invalid` and showed
  `Studio cannot contain runtime connection or credential field 'secret'`.
- Publish of independent `studio-ui-probe-agent` returned 201 and listed
  `v1 · DRAFT · 未提升` with notice that it will not bind new conversations.
- **设为运行版本** promoted it to `v1 · ACTIVE · 运行中`. Builtin `general-agent` v1
  remained the conversation runtime binding.
- Remounting Studio shows **正在加载目录…** then 23 versions. Skill tab lists the 14
  builtin Skills without resetting the Agent catalog. Composer still has no Agent picker.

## Remaining risks

- Staging deploy and human security sign-off remain operator-owned from Phase 25.
- Vendor IM HTTP POST still requires a real tenant application.
- Obsion Eval as a product console was delivered in Phase 35.
