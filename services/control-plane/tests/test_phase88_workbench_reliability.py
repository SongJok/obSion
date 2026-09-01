"""Phase 88: Alpha.1 Workbench reliability hardening.

Static boundary tests pinning the reliability contract of the Workbench,
Desktop shell, and IDE extension after the Phase 88 hardening pass: bounded
requests, route-level error boundaries, per-domain degradation, visible
stream fallback, operator-entered governance declarations, and no stale
async results.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from obsion.release.notes import validate_release_notes

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web" / "src"
DESKTOP = ROOT / "apps" / "desktop" / "src"
IDE = ROOT / "apps" / "ide-extension" / "src"


def _read(base: Path, relative: str) -> str:
    return (base / relative).read_text(encoding="utf-8")


def test_web_has_route_level_error_loading_and_not_found_boundaries() -> None:
    error = _read(WEB, "app/error.tsx")
    assert error.startswith('"use client"')
    # This Next.js version passes retry(), not reset(), to error boundaries.
    assert "retry" in error
    assert "route-fallback" in error
    assert "console.error" in error
    assert "localStorage" not in error
    not_found = _read(WEB, "app/not-found.tsx")
    assert 'href="/"' in not_found
    loading = _read(WEB, "app/loading.tsx")
    assert "session-checking" in loading
    styles = _read(WEB, "app/globals.css")
    assert ".route-fallback" in styles


def test_api_requests_are_bounded_and_normalized() -> None:
    api = _read(WEB, "lib/api.ts")
    assert "AbortSignal.timeout" in api
    assert "DEFAULT_TIMEOUT_MS" in api
    assert "LONG_RUNNING_TIMEOUT_MS" in api
    assert '"request_timeout"' in api
    assert '"network_error"' in api
    assert '"invalid_response"' in api
    # Long-running ingest/eval mutations get the extended budget explicitly.
    assert api.count("timeoutMs: LONG_RUNNING_TIMEOUT_MS") >= 6
    # Session transport invariants from Phase 5 still hold.
    assert 'credentials: "include"' in api
    assert "localStorage" not in api
    assert "sessionStorage" not in api


def test_eval_view_results_are_generation_guarded_and_errors_caught() -> None:
    view = _read(WEB, "components/eval-view.tsx")
    assert "resultsGeneration" in view
    assert "resultsLoading" in view
    assert ".then(setResults)" not in view
    assert "void loadResults(item.id)" in view
    # loadCases must not reject unhandled from the initial load effect.
    assert "无法读取评测案例" in view


def test_admin_console_degrades_per_domain_instead_of_failing_closed() -> None:
    view = _read(WEB, "components/admin-view.tsx")
    assert "Promise.allSettled" in view
    assert "ADMIN_DOMAINS" in view
    assert "部分治理域暂时不可用" in view
    assert "所有治理域请求均失败" in view
    assert "Promise.all(" not in view
    styles = _read(WEB, "app/globals.css")
    assert ".notice.warning" in styles


def test_data_and_code_views_distinguish_loading_empty_and_no_match() -> None:
    data = _read(WEB, "components/data-view.tsx")
    assert "正在加载指标目录…" in data
    assert "没有匹配的已验证指标" in data
    code = _read(WEB, "components/code-view.tsx")
    assert "正在加载仓库…" in code
    assert "没有匹配的授权符号" in code
    assert "setSearched(true)" in code


def test_runtime_inspector_resets_detail_selection_on_run_change() -> None:
    inspector = _read(WEB, "components/runtime-inspector.tsx")
    assert "lastRunId" in inspector
    assert "setSelectedEvidence(undefined);" in inspector
    assert "setSelectedArtifact(undefined);" in inspector


def test_stream_fallback_state_is_visible_during_active_runs() -> None:
    workbench = _read(WEB, "components/workbench.tsx")
    assert 'setStreamState("live")' in workbench
    assert 'setStreamState("polling")' in workbench
    assert 'setStreamState("interrupted")' in workbench
    assert "streamState={streamState}" in workbench
    inspector = _read(WEB, "components/runtime-inspector.tsx")
    assert "StreamStateChip" in inspector
    assert "实时流" in inspector
    assert "轮询同步" in inspector
    assert "同步中断" in inspector
    styles = _read(WEB, "app/globals.css")
    assert ".stream-state-chip" in styles


def test_action_preflight_declaration_is_operator_entered() -> None:
    view = _read(WEB, "components/actions-view.tsx")
    assert "已核对目标、变更内容、影响范围和补偿方案，申请独立审批。" not in view
    assert "preflightReason" in view
    assert "核对声明" in view
    assert "reason.trim().length < 10" in view
    assert "api.actions.preflight(detail.action.id, declaration)" in view


def test_knowledge_upload_is_single_flight_and_rearmable() -> None:
    view = _read(WEB, "components/knowledge-view.tsx")
    assert "if (uploading) return;" in view
    assert "setUploading(true)" in view
    assert 'fileRef.current.value = ""' in view
    assert "disabled={uploading}" in view


def test_desktop_shell_guards_every_action_and_requires_human_reason() -> None:
    shell = _read(DESKTOP, "shell.ts")
    assert "async function guard(action)" in shell
    assert "button.disabled = true" in shell
    assert "Approved from Desktop" not in shell
    assert "请填写审批说明" in shell
    assert ".then((body) =>" not in shell


def test_ide_approval_reasons_are_human_entered_or_cancelled() -> None:
    commands = _read(IDE, "commands.ts")
    assert "Approved from IDE" not in commands
    assert "Rejected from IDE" not in commands
    assert "human-entered reason" in commands
    assert "Approval decision cancelled" in commands


def test_release_notes_and_project_status_track_phase88() -> None:
    result = validate_release_notes(ROOT / "docs" / "release" / "0.88.0-dev.yaml", ROOT)
    assert result["version"] == "0.88.0-dev"
    status = yaml.safe_load((ROOT / "docs" / "project-status.yaml").read_text(encoding="utf-8"))
    assert status["version"] == "0.91.0-dev"
    assert status["current_phase"] == "phase-91"
    assert "phase-88" in status["completed_phases"]
