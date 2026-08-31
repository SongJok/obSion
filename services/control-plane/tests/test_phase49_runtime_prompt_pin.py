from __future__ import annotations

import ast
import asyncio
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from obsion.common.errors import ConflictError
from obsion.registry.prompt_pins import (
    SYSTEM_POLICY_PROMPT_NAME,
    load_pinned_templates,
    names_for_agent_spec,
    prompt_fingerprint,
)

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"
WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"


def _thread(client: TestClient, title: str) -> dict:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": title, "description": "Phase 49 prompt pin"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": title},
    )
    assert thread.status_code == 201, thread.text
    return thread.json()


def _system_pin(run: dict) -> dict:
    pins = run.get("prompt_pins") or []
    return next(item for item in pins if item["name"] == SYSTEM_POLICY_PROMPT_NAME)


def test_names_for_agent_spec_always_include_system_policy() -> None:
    assert names_for_agent_spec({}) == (SYSTEM_POLICY_PROMPT_NAME,)
    assert names_for_agent_spec({"prompts": ["obsion-system-policy", "custom-prompt"]}) == (
        SYSTEM_POLICY_PROMPT_NAME,
        "custom-prompt",
    )
    assert prompt_fingerprint(
        [{"name": "a", "version_id": "1"}, {"name": "b", "version_id": "2"}]
    ) != prompt_fingerprint([{"name": "a", "version_id": "9"}])


def test_invalid_runtime_pin_is_conflict() -> None:
    async def _load() -> None:
        await load_pinned_templates(
            None,  # type: ignore[arg-type]
            uuid4(),
            [{"version_id": "not-a-uuid", "checksum_sha256": "abc"}],
        )

    with pytest.raises(ConflictError) as caught:
        asyncio.run(_load())
    assert caught.value.code == "prompt_pin_mismatch"


def test_turn_pins_prompt_snapshot_and_replay_copies_it(client: TestClient) -> None:
    thread = _thread(client, "Prompt pin thread")
    created = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={"input": "你好"},
    )
    assert created.status_code == 202, created.text
    first = created.json()["run"]
    pin = _system_pin(first)
    assert pin["version"] == 1
    checksum = pin["checksum_sha256"]

    for _ in range(100):
        current = client.get(f"/api/v1/runs/{first['id']}")
        assert current.status_code == 200, current.text
        if current.json()["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("Pinned run did not reach a terminal state")

    published = client.post(
        "/api/v1/admin/prompts",
        json={
            "name": SYSTEM_POLICY_PROMPT_NAME,
            "display_name": "Obsion system policy",
            "template": "Cite authorized evidence only. Never invent facts.",
            "variables_schema": {"type": "object"},
        },
    )
    assert published.status_code == 201, published.text
    assert published.json()["version"] == 2

    original = client.get(f"/api/v1/runs/{first['id']}")
    assert original.status_code == 200, original.text
    assert _system_pin(original.json())["version"] == 1
    assert _system_pin(original.json())["checksum_sha256"] == checksum

    second_turn = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={"input": "继续"},
    )
    assert second_turn.status_code == 202, second_turn.text
    assert _system_pin(second_turn.json()["run"])["version"] == 2

    replay = client.post(f"/api/v1/runs/{first['id']}/replay")
    assert replay.status_code == 202, replay.text
    assert _system_pin(replay.json())["version"] == 1
    assert _system_pin(replay.json())["checksum_sha256"] == checksum
    assert replay.json()["replay_of_run_id"] == first["id"]


def test_eval_can_pin_distinct_prompt_versions(client: TestClient) -> None:
    catalog = client.get("/api/v1/eval/catalog")
    assert catalog.status_code == 200, catalog.text
    body = catalog.json()
    agent = next(item for item in body["agents"] if item["name"] == "general-agent")
    profile = next(item for item in body["model_profiles"] if item["name"] == "reasoning-high")
    policy = next(item for item in body["prompts"] if item["name"] == SYSTEM_POLICY_PROMPT_NAME)
    assert policy["version"] == 1

    dataset = client.post(
        "/api/v1/eval/datasets",
        json={"name": "prompt-pin-eval", "domain": "foundation", "description": "Prompt pins"},
    )
    assert dataset.status_code == 201, dataset.text
    dataset_id = dataset.json()["id"]
    case = client.post(
        f"/api/v1/eval/datasets/{dataset_id}/cases",
        json={
            "external_id": "route-knowledge-pin",
            "evaluator": "ROUTING",
            "input_payload": {"question": "Summarize the employee handbook"},
            "expected": {"route": "KNOWLEDGE"},
            "fixtures": {},
        },
    )
    assert case.status_code == 201, case.text

    published = client.post(
        "/api/v1/admin/prompts",
        json={
            "name": SYSTEM_POLICY_PROMPT_NAME,
            "display_name": "Obsion system policy",
            "template": "Return JSON. Cite DOCUMENT evidence only.",
            "variables_schema": {"type": "object"},
        },
    )
    assert published.status_code == 201, published.text

    baseline = client.post(
        f"/api/v1/eval/datasets/{dataset_id}/runs",
        json={
            "agent_version_id": agent["version_id"],
            "model_profile_id": profile["id"],
            "application_revision": "prompt-v1",
            "prompt_pins": {SYSTEM_POLICY_PROMPT_NAME: 1},
        },
    )
    assert baseline.status_code == 201, baseline.text
    candidate = client.post(
        f"/api/v1/eval/datasets/{dataset_id}/runs",
        json={
            "agent_version_id": agent["version_id"],
            "model_profile_id": profile["id"],
            "application_revision": "prompt-v2",
            "prompt_pins": {SYSTEM_POLICY_PROMPT_NAME: 2},
        },
    )
    assert candidate.status_code == 201, candidate.text
    assert baseline.json()["configuration_snapshot"]["prompts"][0]["version"] == 1
    assert candidate.json()["configuration_snapshot"]["prompts"][0]["version"] == 2

    compared = client.post(
        "/api/v1/eval/compare",
        json={
            "baseline_run_id": baseline.json()["id"],
            "candidate_run_id": candidate.json()["id"],
        },
    )
    assert compared.status_code == 200, compared.text
    assert compared.json()["agent_changed"] is False
    assert compared.json()["prompt_changed"] is True

    unknown = client.post(
        f"/api/v1/eval/datasets/{dataset_id}/runs",
        json={
            "agent_version_id": agent["version_id"],
            "model_profile_id": profile["id"],
            "application_revision": "prompt-unknown",
            "prompt_pins": {"not-a-declared-prompt": 1},
        },
    )
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "registry_spec_invalid"


def test_prompt_pin_is_not_a_latest_lookup_in_synthesize() -> None:
    source = (_SOURCE_ROOT / "harness" / "runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append(node.module)
    assert "obsion.registry.prompt_pins" in imports
    assert "load_pinned_templates" in source
    assert "You are Obsion. Use only supplied evidence." not in source
    eval_view = (WEB_ROOT / "src" / "components" / "eval-view.tsx").read_text(encoding="utf-8")
    assert "prompt_pins" in eval_view
    assert "prompt_changed" in eval_view
    assert "selectedAgent" not in eval_view
    studio = (_SOURCE_ROOT / "application" / "studio.py").read_text(encoding="utf-8")
    assert "PromptDefinition.active_version" not in studio
