from __future__ import annotations

import ast
import time
from pathlib import Path

from fastapi.testclient import TestClient

from obsion.model_gateway.context import BudgetAction, ContextBuilder, ContextSegment, TrustLevel

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"
WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"


def _wait_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach a terminal state: {run}")


def test_token_budget_manager_is_deterministic_and_not_a_second_model_loop() -> None:
    source = (_SOURCE_ROOT / "model_gateway" / "context.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    banned = {"httpx", "openai", "anthropic", "obsion.model_gateway.gateway"}
    assert banned.isdisjoint(imports)
    assert "eval(" not in source
    assert "str.format" not in source
    assert "Template(" not in source
    assert "complete(" not in source
    assert BudgetAction.KEEP.value == "KEEP"
    pack = ContextBuilder(character_budget=12).pack(
        [
            ContextSegment(TrustLevel.SYSTEM, "12345678", "policy", priority=100),
            ContextSegment(TrustLevel.USER, "abcdefgh", "current-user", priority=10),
        ]
    )
    again = ContextBuilder(character_budget=12).pack(
        [
            ContextSegment(TrustLevel.SYSTEM, "12345678", "policy", priority=100),
            ContextSegment(TrustLevel.USER, "abcdefgh", "current-user", priority=10),
        ]
    )
    assert pack.as_dict() == again.as_dict()
    assert pack.as_dict()["method"] == "extractive"


def test_harness_pins_context_budget_on_knowledge_run(client: TestClient) -> None:
    document = client.post(
        "/api/v1/knowledge/documents",
        files={
            "file": (
                "budget.md",
                b"# Token budget\nContext Builder must keep, compress, summarize, or drop.",
                "text/markdown",
            )
        },
        data={
            "source": "phase51",
            "external_id": "token-budget-policy",
            "title": "Token budget",
            "classification": "INTERNAL",
            "acl": '{"organization": true}',
        },
    )
    assert document.status_code == 201, document.text
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Phase 51 budget", "description": "Context token budget"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Budget thread"},
    )
    assert thread.status_code == 201, thread.text
    created = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "What must the Token Budget Manager decide?"},
    )
    assert created.status_code == 202, created.text
    first = created.json()["run"]
    assert first.get("context_budget") in ({}, None)
    run = _wait_terminal(client, first["id"])
    assert run["status"] == "COMPLETED", f"{run.get('error_code')}: {run.get('error_message')}"
    budget = run["context_budget"]
    assert budget["method"] == "extractive"
    assert budget["used"] <= budget["budget"]
    actions = {item["source"]: item["action"] for item in budget["decisions"]}
    assert actions.get("platform-policy") == "KEEP"
    assert actions.get("current-user") == "KEEP"
    assert "DROP" in {item["action"] for item in budget["decisions"]} or "KEEP" in actions.values()

    replay = client.post(f"/api/v1/runs/{run['id']}/replay")
    assert replay.status_code == 202, replay.text
    assert replay.json()["context_budget"] == budget
    assert replay.json()["replay_of_run_id"] == run["id"]


def test_inspector_and_runtime_surface_the_budget_ledger() -> None:
    harness = (_SOURCE_ROOT / "harness" / "runtime.py").read_text(encoding="utf-8")
    assert "_pin_context_budget" in harness
    assert "context_budget_counter" in harness
    assert ".build(segments)" not in harness
    inspector = (WEB_ROOT / "src" / "components" / "runtime-inspector.tsx").read_text(
        encoding="utf-8"
    )
    assert "Token 预算账本" in inspector
    assert "context_budget" in inspector
    assert "抽取式摘要" in inspector
    types = (WEB_ROOT / "src" / "lib" / "types.ts").read_text(encoding="utf-8")
    assert "context_budget" in types
    assert "SUMMARIZE" in types
