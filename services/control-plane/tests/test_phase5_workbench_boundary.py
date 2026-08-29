from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).parents[3]
_WEB_SOURCE = _REPOSITORY_ROOT / "apps/web/src"


def _read(relative_path: str) -> str:
    return (_WEB_SOURCE / relative_path).read_text(encoding="utf-8")


def test_phase5_home_is_session_gated_and_has_one_three_column_workbench() -> None:
    page = _read("app/page.tsx")
    workbench = _read("components/workbench.tsx")

    assert "<SessionGate" in page
    assert "<Workbench" not in page
    assert '<div className="app-shell">' in workbench
    assert "<Sidebar" in workbench
    assert '<main className="chat-panel">' in workbench
    assert "<RuntimeInspector" in workbench


def test_phase5_runtime_renders_plan_steps_status_and_cost() -> None:
    inspector = _read("components/runtime-inspector.tsx")

    assert "function RuntimeTimeline" in inspector
    assert "steps.map" in inspector
    assert "step.status.toLowerCase()" in inspector
    assert "run.cost_amount" in inspector
    assert "latestEvent?.name" in inspector


def test_phase5_browser_never_persists_or_replays_the_access_token() -> None:
    web_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in _WEB_SOURCE.rglob("*")
        if path.suffix in {".ts", ".tsx"}
    )

    assert "localStorage" not in web_sources
    assert "sessionStorage" not in web_sources
    assert "Authorization: `Bearer" not in web_sources
    assert 'credentials: "include"' in _read("lib/api.ts")


def test_phase5_mobile_shell_prevents_page_level_horizontal_scrolling() -> None:
    styles = _read("app/globals.css")

    assert "html, body { width: 100%; max-width: 100%;" in styles
    assert ".app-shell { display: flex; width: 100%; max-width: 100%;" in styles
    assert "100vw" not in styles
    assert "@media (max-width: 880px)" in styles
    assert ".runtime-inspector.mobile-visible { display: flex; }" in styles
    assert ".navigation-scrim" in styles
    assert ".inspector-scrim" in styles
