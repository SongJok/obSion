"""Phase 97: broader Workbench interaction tests.

The Web test stack now exercises real component interactions — composer
keyboard behaviour, claim-action provenance, collaboration task creation,
and the Automation workflow lifecycle — against a mocked API boundary.
Writing those tests surfaced a genuine defect: the collaboration view's
mutation handler set its actionable error message and *then* refreshed,
and the refresh clears notices on entry, so version-conflict,
assignee-invalid, and source-Run mismatch guidance vanished before anyone
could read it. The handler now refreshes first and surfaces the message
after; the interaction suites pin the ordering and the Automation API
contracts so neither can silently regress.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from obsion.release.notes import validate_release_notes

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"


def _read(relative: str) -> str:
    return (WEB / relative).read_text(encoding="utf-8")


def test_interaction_suite_covers_workbench_surfaces() -> None:
    suite = _read("tests/workbench-interactions.test.tsx")
    for marker in (
        "Composer interactions",
        "RuntimeInspector claim actions",
        "CollaborationView task creation",
        "fireEvent",
        "cleanup",
        "停止运行",
        "添加上下文 支付周报",
        "移除附件 支付周报",
        "source_run_id",
        "在协作中查看",
        "workspace_source_run_mismatch",
        "workspace_task_assignee_invalid",
        "指派的成员必须是该工作空间的在职成员，请刷新成员列表后重试。",
        "RuntimeInspector tabs",
        "exposes tabs with roving keyboard focus",
        "opens the context, evidence, memory, claim evidence, and artifact details",
    ):
        assert marker in suite


def test_root_orchestration_suite_pins_generation_and_ownership_boundaries() -> None:
    suite = _read("tests/workbench-orchestration-interactions.test.tsx")
    for marker in (
        "Workbench root orchestration ownership",
        "keeps only the newest Workspace thread response",
        "commits one complete Thread and inspection snapshot after reverse completion",
        "ignores a slower source-Run inspection after a newer source Run commits",
        "rejects a mismatched inspection atomically without replacing the prior Run",
        "does not continue loading a superseded source Run",
        "ignores stream events that belong to another Run",
        "resumes stream and REST reconciliation when opening a Thread with an active Run",
        "rejects Thread history feedback owned by another Run atomically",
        "ignores a stale submit after switching Workspace",
        "ignores a stale cancel response after switching Workspace",
        "ignores stale replay pending and errors after switching Workspace",
        "ignores stale feedback pending and errors after switching Workspace",
        "rejects feedback returned for another Run without committing it",
        "keeps Context Picker results scoped to the newest Workspace",
        "stops a stale multi-file upload and never attaches it to another Workspace",
        "allows only one submit while Thread and Run creation are in flight",
        "does not expose a server-created Thread until its first Run exists",
        "opens a Collaboration source Run through its owning Thread",
        "deferred<",
    ):
        assert marker in suite

    workbench = _read("src/components/workbench.tsx")
    for marker in (
        "interface InspectionSnapshot",
        "const selectionGeneration = useRef(0)",
        "const contextGeneration = useRef(0)",
        "const uploadGeneration = useRef(0)",
        "const feedbackGeneration = useRef(0)",
        "const threadLifecycleGeneration = useRef(0)",
        "const submitInFlight = useRef(false)",
        "const pollRunRef = useRef<(",
        "pollRunRef.current = pollRun",
        "[pollRun]",
        "submitting={submitting}",
        "const openScopedRun",
        "await openThread(selected, runId)",
        "const keepsVerifiedProjection = thread?.id === selected.id",
        "assertInspectionOwnership(snapshot)",
        "applyInspection(snapshot)",
        "event.run_id !== expectedRunId",
        "assertRunFeedback(item.id, feedback)",
    ):
        assert marker in workbench


def test_interaction_suite_pins_notice_survival_after_refresh() -> None:
    suite = _read("tests/workbench-interactions.test.tsx")
    assert "keeps the version-conflict guidance visible after the refresh" in suite
    assert "记录已被其他成员更新，已为你刷新到最新版本。请确认后重试。" in suite
    assert "listTasks.mock.calls.length" in suite


def test_automation_interaction_suite_covers_operator_lifecycle() -> None:
    suite = _read("tests/automation-interactions.test.tsx")
    for marker in (
        "AutomationView interactions",
        "publishes an older version from the versions card",
        "workflow: workflow({ active_version: 1 })",
        "triggers a manual run with a validated JSON payload",
        "rejects malformed trigger payloads without calling the API",
        "creates a schedule from a preset without a version pin",
        "requires the two-step confirm before retiring a paused workflow",
        "derives a new immutable version from an existing one",
    ):
        assert marker in suite


def test_admin_interaction_suite_covers_governed_operations() -> None:
    suite = _read("tests/admin-interactions.test.tsx")
    for marker in (
        "AdminView interactions",
        "preserves partial-domain degradation",
        "creates an IM principal binding with the stable sender id",
        'sender_id: "ou_test_sender"',
        "revokes an active IM binding",
        "shows connector discovery without auto-binding a capability",
        "runs connector health, scan, and promotion operations",
        "probeConnectorHealth",
        "scanConnectorPlugin",
        "promoteConnectorPlugin",
    ):
        assert marker in suite


def test_action_interaction_suite_preserves_v1_change_control() -> None:
    suite = _read("tests/actions-interactions.test.tsx")
    for marker in (
        "ActionsView interactions",
        "without exposing a production option",
        'queryByRole("option", { name: "生产环境" })',
        "requires an operator declaration before preflight",
        "submits an independent approval decision",
        "requests rollback with a bounded human reason",
        "cancels only through the governed action endpoint",
        "idempotency_key",
        "actions.preflight",
        "actions.decide",
        "actions.rollback",
        "actions.cancel",
    ):
        assert marker in suite


def test_action_modals_expose_accessible_dialog_contracts() -> None:
    view = _read("src/components/actions-view.tsx")
    assert view.count('role="dialog"') == 3
    assert view.count('aria-modal="true"') == 3
    for marker in (
        'aria-labelledby="create-action-title"',
        'aria-labelledby="action-decision-title"',
        'aria-labelledby="action-reason-title"',
    ):
        assert marker in view


def test_studio_interactions_pin_immutable_version_governance() -> None:
    suite = _read("tests/studio-interactions.test.tsx")
    for marker in (
        "StudioView interactions",
        "uses accessible roving tabs",
        "keeps Workflow validation-only",
        "publishes a new immutable Skill version without promoting it",
        "no runtime traffic split",
        "clears stale baselines",
        "promotes only the explicitly selected immutable version",
        "without rewriting it",
    ):
        assert marker in suite
    view = _read("src/components/studio-view.tsx")
    for marker in (
        'role="tablist" aria-label="Studio 清单类型"',
        'role="tab"',
        'role="tabpanel"',
        'setCompareVersion("")',
        'event.key === "ArrowRight"',
        'event.key === "ArrowLeft"',
    ):
        assert marker in view


def test_eval_interactions_pin_dataset_scoping_and_input_validation() -> None:
    suite = _read("tests/eval-interactions.test.tsx")
    for marker in (
        "EvalView interactions",
        "loads the default dataset once",
        "without retaining an old baseline",
        "rejects malformed JSON before transport",
        "pinned Agent, Prompt, model, baseline, and Run bindings",
        "rejects non-object or non-string run bindings",
        "compares distinct same-dataset Runs",
        "clears the baseline on dataset switch",
    ):
        assert marker in suite
    view = _read("src/components/eval-view.tsx")
    for marker in (
        "function parseObjectJson",
        "function parseRunBindings",
        'setBaselineRunId("")',
        "runs.filter((item) => item.id !== selectedRunId)",
        'if (baselineRunId === item.id) setBaselineRunId("")',
    ):
        assert marker in view


def test_knowledge_interactions_prevent_stale_evidence_attribution() -> None:
    suite = _read("tests/knowledge-interactions.test.tsx")
    for marker in (
        "KnowledgeView interactions",
        "searches the trimmed authorized query",
        "renders recorded provenance",
        "clears stale Evidence when a later search fails",
        "classification and ACL metadata",
        "dedicated API",
        "synchronizes a Feishu knowledge space",
    ):
        assert marker in suite
    view = _read("src/components/knowledge-view.tsx")
    assert "setResults([]);" in view
    assert "api.knowledgeSearch(query.trim())" in view
    assert 'aria-label="上传知识文档"' in view
    assert view.index("setResults([]);") < view.index("api.knowledgeSearch(query.trim())")


def test_data_interactions_pin_lineage_generation_and_accessibility() -> None:
    suite = _read("tests/data-interactions.test.tsx")
    for marker in (
        "DataView interactions",
        "filters only verified metrics",
        "governed metric definition in a named dialog",
        "roving tabs",
        "read-only lineage",
        "without inventing a graph",
        "ignores a slower stale lineage response",
    ):
        assert marker in suite
    view = _read("src/components/data-view.tsx")
    for marker in (
        "const lineageGeneration = useRef(0)",
        "generation === lineageGeneration.current",
        'role="tablist" aria-label="指标详情页签"',
        'role="tab"',
        'role="tabpanel"',
        'event.key === "ArrowRight"',
        'event.key === "ArrowLeft"',
    ):
        assert marker in view


def test_code_interactions_prevent_cross_query_symbol_attribution() -> None:
    suite = _read("tests/code-interactions.test.tsx")
    for marker in (
        "CodeView interactions",
        "trimmed symbol query",
        "authorized empty result from a transport failure",
        "clears stale symbols before a later search failure",
        "ignores a slower stale search response",
    ):
        assert marker in suite
    view = _read("src/components/code-view.tsx")
    for marker in (
        "const searchGeneration = useRef(0)",
        "generation === searchGeneration.current",
        "api.searchCodeSymbols(query.trim())",
        "setResults([]);",
        "!error",
    ):
        assert marker in view
    assert view.index("setResults([]);") < view.index("api.searchCodeSymbols(query.trim())")


def test_workspace_asset_interactions_pin_paths_history_and_acl_metadata() -> None:
    suite = _read("tests/workspace-assets-interactions.test.tsx")
    for marker in (
        "FilesView interactions",
        "immutable history",
        "safe default path",
        "downloads through the artifact API",
        "ArtifactsView interactions",
        "governed kind and query",
        "ACL-classified artifact",
        "preserves a selected artifact by id",
    ):
        assert marker in suite
    files = _read("src/components/files-view.tsx")
    artifacts = _read("src/components/artifacts-view.tsx")
    assert 'useState("")' in files
    assert 'aria-label="上传工作区文件"' in files
    assert 'aria-label="上传工作区产物"' in artifacts
    assert 'form.set("classification", workspace.classification || "INTERNAL")' in files
    assert 'form.set("classification", workspace.classification || "INTERNAL")' in artifacts


def test_workspace_projection_interactions_pin_persisted_fact_boundaries() -> None:
    suite = _read("tests/workspace-projections-interactions.test.tsx")
    for marker in (
        "Workspace artifact projections",
        "persisted REPORT artifacts",
        "unique valid Dashboard panel references",
        "clears a failed Dashboard panel error",
        "persisted validated SQL without executing it",
        "Workspace Evidence and Event projections",
        "selected immutable envelope",
        "persisted Event payloads",
    ):
        assert marker in suite
    dashboard = _read("src/components/dashboards-view.tsx")
    for marker in (
        "...new Set(",
        'typeof id === "string"',
        ".map((id) => id.trim())",
        'reportError("")',
        "api.getArtifact(id)",
    ):
        assert marker in dashboard


def test_runtime_inspector_tabs_use_the_accessible_keyboard_contract() -> None:
    view = _read("src/components/runtime-inspector.tsx")
    for marker in (
        'role="tablist" aria-label="运行详情页签"',
        'role="tab"',
        "aria-selected={tab === item}",
        'role="tabpanel"',
        'event.key === "ArrowRight"',
        'event.key === "ArrowLeft"',
        'event.key === "Home"',
        'event.key === "End"',
    ):
        assert marker in view


def test_mutation_handler_refreshes_before_surfacing_guidance() -> None:
    view = _read("src/components/collaboration-view.tsx")
    # Every ApiError branch must refresh first and surface the message after,
    # because load() clears notices on entry.
    assert view.count("await load();\n          setError(") == 3
    assert 'setError("记录已被其他成员更新' in view
    assert 'setError("指派的成员必须是该工作空间的在职成员' in view
    assert 'setError("来源 Run 必须属于当前工作空间' in view


def test_ci_executes_every_destructive_migration_round_trip() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    matrix_job = workflow.split("  migration-round-trips:", 1)[1].split("\n  containers:", 1)[0]
    for marker in (
        "phase5-auth-session",
        "obsion_phase5_auth_session",
        "OBSION_RUN_PHASE5_MIGRATION_TEST",
        "test_postgres_phase5_auth_session_migration.py",
        "phase79-operator-invocation",
        "obsion_phase79_operator_invocation",
        "OBSION_RUN_PHASE79_MIGRATION_TEST",
        "test_postgres_operator_invocation_migration.py",
        'env "${{ matrix.opt_in }}=1"',
        "alembic -c services/control-plane/alembic.ini check",
    ):
        assert marker in matrix_job
    containers = workflow.split("  containers:", 1)[1].split("\n  java-sdk:", 1)[0]
    assert "migration-round-trips" in containers.split("needs:", 1)[1].split("\n", 1)[0]


def test_release_notes_and_project_status_track_phase97() -> None:
    result = validate_release_notes(ROOT / "docs" / "release" / "0.97.0-dev.yaml", ROOT)
    assert result["version"] == "0.97.0-dev"
    status = yaml.safe_load((ROOT / "docs" / "project-status.yaml").read_text(encoding="utf-8"))
    assert status["version"] == "0.97.0-dev"
    assert status["current_phase"] == "phase-97"
    assert "phase-96" in status["completed_phases"]
