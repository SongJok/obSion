import time
from uuid import UUID

from fastapi.testclient import TestClient

from obsion.config import Settings
from obsion.main import create_app
from obsion.security.auth import get_principal
from obsion.security.identity import Principal


def _create_workspace_and_thread(client: TestClient) -> tuple[dict, dict]:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Thread lifecycle", "description": "Durable investigation history"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Payment investigation"},
    )
    assert thread.status_code == 201, thread.text
    return workspace.json(), thread.json()


def test_thread_lifecycle_is_forkable_audited_and_inspectable(client: TestClient) -> None:
    workspace, thread = _create_workspace_and_thread(client)
    created_turn = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={"input": "Investigate the current payment evidence."},
    )
    assert created_turn.status_code == 202, created_turn.text
    turn_id = created_turn.json()["turn"]["id"]

    forked = client.post(
        f"/api/v1/threads/{thread['id']}/fork",
        json={"from_turn_id": turn_id, "title": "Payment investigation · alternative"},
    )
    assert forked.status_code == 201, forked.text
    child = forked.json()
    assert child["parent_thread_id"] == thread["id"]
    assert child["forked_from_turn_id"] == turn_id
    assert child["status"] == "ACTIVE"
    source = client.get(f"/api/v1/workspaces/{workspace['id']}/threads?include_archived=true")
    source_thread = next(item for item in source.json() if item["id"] == thread["id"])
    assert source_thread["status"] == "ARCHIVED"
    assert source_thread["archived_at"] is not None

    rejected_after_fork = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={"input": "Forking makes the source branch read-only."},
    )
    assert rejected_after_fork.status_code == 409, rejected_after_fork.text
    assert rejected_after_fork.json()["code"] == "thread_archived"

    inherited_turns = client.get(f"/api/v1/threads/{child['id']}/turns")
    assert inherited_turns.status_code == 200, inherited_turns.text
    assert [item["id"] for item in inherited_turns.json()] == [turn_id]
    inherited_runs = client.get(f"/api/v1/threads/{child['id']}/runs")
    assert inherited_runs.status_code == 200, inherited_runs.text
    assert [item["id"] for item in inherited_runs.json()] == [created_turn.json()["run"]["id"]]

    run_id = created_turn.json()["run"]["id"]
    for _ in range(100):
        run = client.get(f"/api/v1/runs/{run_id}").json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.05)
    assert run["status"] == "COMPLETED", run

    child_events = client.get(f"/api/v1/threads/{child['id']}/events")
    assert child_events.status_code == 200, child_events.text
    assert [(item["sequence"], item["name"]) for item in child_events.json()] == [
        (1, "thread.forked")
    ]
    assert child_events.json()[0]["payload"] == {
        "parent_thread_id": thread["id"],
        "forked_from_turn_id": turn_id,
    }

    archived = client.post(f"/api/v1/threads/{thread['id']}/archive")
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "ARCHIVED"
    assert archived.json()["archived_at"] is not None

    active_threads = client.get(f"/api/v1/workspaces/{workspace['id']}/threads")
    assert active_threads.status_code == 200, active_threads.text
    assert [item["id"] for item in active_threads.json()] == [child["id"]]

    all_threads = client.get(f"/api/v1/workspaces/{workspace['id']}/threads?include_archived=true")
    assert all_threads.status_code == 200, all_threads.text
    assert {item["id"]: item["status"] for item in all_threads.json()} == {
        thread["id"]: "ARCHIVED",
        child["id"]: "ACTIVE",
    }

    resumed = client.post(f"/api/v1/threads/{thread['id']}/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "ACTIVE"
    assert resumed.json()["archived_at"] is None

    later_turn = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={"input": "This later parent Turn must not leak into an existing fork."},
    )
    assert later_turn.status_code == 202, later_turn.text
    frozen_child_turns = client.get(f"/api/v1/threads/{child['id']}/turns")
    assert [item["id"] for item in frozen_child_turns.json()] == [turn_id]

    child_turn = client.post(
        f"/api/v1/threads/{child['id']}/turns",
        json={"input": "Continue only from the branch point context."},
    )
    assert child_turn.status_code == 202, child_turn.text
    child_run_id = child_turn.json()["run"]["id"]
    conversation = client.get(f"/api/v1/runs/{child_run_id}/conversation")
    assert conversation.status_code == 200, conversation.text
    assert len(conversation.json()) == 1
    snapshot = conversation.json()[0]
    assert snapshot["source_thread_id"] == thread["id"]
    assert snapshot["source_turn_id"] == turn_id
    assert snapshot["source_run_id"] == run_id
    assert snapshot["user_content"] == "Investigate the current payment evidence."
    assert snapshot["assistant_content"]
    assert "later parent Turn" not in snapshot["user_content"]
    assert len(snapshot["content_fingerprint"]) == 64

    child_run = child_turn.json()["run"]
    for _ in range(100):
        child_run = client.get(f"/api/v1/runs/{child_run_id}").json()
        if child_run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.05)
    assert child_run["status"] == "COMPLETED", child_run
    context_event = next(
        item
        for item in client.get(f"/api/v1/runs/{child_run_id}/events").json()
        if item["name"] == "context.resolved"
    )
    assert context_event["payload"]["conversation_snapshots"] == [
        {
            "id": snapshot["id"],
            "ordinal": 1,
            "source_thread_id": thread["id"],
            "source_turn_id": turn_id,
            "source_run_id": run_id,
            "content_fingerprint": snapshot["content_fingerprint"],
            "classification": snapshot["classification"],
        }
    ]

    replay = client.post(f"/api/v1/runs/{child_run_id}/replay")
    assert replay.status_code == 202, replay.text
    replay_id = replay.json()["id"]
    replay_run = replay.json()
    for _ in range(100):
        replay_run = client.get(f"/api/v1/runs/{replay_id}").json()
        if replay_run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.05)
    assert replay_run["status"] == "COMPLETED", replay_run
    replay_conversation = client.get(f"/api/v1/runs/{replay_id}/conversation").json()
    assert len(replay_conversation) == 1
    assert replay_conversation[0]["id"] != snapshot["id"]
    assert replay_conversation[0]["content_fingerprint"] == snapshot["content_fingerprint"]
    replay_completed = next(
        item
        for item in client.get(f"/api/v1/runs/{replay_id}/events").json()
        if item["name"] == "run.replay.completed"
    )
    assert replay_completed["payload"]["conversation"] == 1
    child_runs = client.get(f"/api/v1/threads/{child['id']}/runs").json()
    same_turn_runs = [
        item for item in child_runs if item["turn_id"] == child_turn.json()["turn"]["id"]
    ]
    assert [item["id"] for item in same_turn_runs] == [child_run_id, replay_id]

    nested_fork = client.post(
        f"/api/v1/threads/{child['id']}/fork",
        json={"from_turn_id": turn_id, "title": "Nested alternative"},
    )
    assert nested_fork.status_code == 201, nested_fork.text
    assert nested_fork.json()["forked_from_turn_id"] == turn_id
    nested_turns = client.get(f"/api/v1/threads/{nested_fork.json()['id']}/turns")
    assert [item["id"] for item in nested_turns.json()] == [turn_id]

    events = client.get(f"/api/v1/threads/{thread['id']}/events")
    assert events.status_code == 200, events.text
    assert [(item["sequence"], item["name"]) for item in events.json()] == [
        (1, "thread.created"),
        (2, "thread.archived"),
        (3, "thread.resumed"),
    ]
    after_archive = client.get(f"/api/v1/threads/{thread['id']}/events?after_sequence=2")
    assert [item["name"] for item in after_archive.json()] == ["thread.resumed"]

    audit = client.get("/api/v1/admin/audit?limit=1000")
    assert audit.status_code == 200, audit.text
    lifecycle_actions = {
        item["action"]
        for item in audit.json()
        if item["resource_id"] in {thread["id"], child["id"]}
    }
    assert lifecycle_actions == {
        "thread.create",
        "thread.fork",
        "thread.archive",
        "thread.resume",
    }


def test_manual_archive_rejects_a_thread_with_an_active_run(client: TestClient) -> None:
    _, thread = _create_workspace_and_thread(client)
    created_turn = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={"input": "Keep this Run active while archive is attempted."},
    )
    assert created_turn.status_code == 202, created_turn.text

    active_archive = client.post(f"/api/v1/threads/{thread['id']}/archive")

    assert active_archive.status_code == 409, active_archive.text
    assert active_archive.json()["code"] == "thread_has_active_run"


def test_thread_lifecycle_preserves_tenant_boundary(client: TestClient) -> None:
    workspace, thread = _create_workspace_and_thread(client)
    created_turn = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={"input": "Tenant-isolated context."},
    )
    assert created_turn.status_code == 202, created_turn.text
    run_id = created_turn.json()["run"]["id"]
    other_tenant = Principal(
        id=UUID("00000000-0000-7000-8000-000000000099"),
        organization_id=UUID("00000000-0000-7000-8000-000000000099"),
        external_id="cross-tenant-thread-reader",
        display_name="Cross-tenant Thread Reader",
        permissions=frozenset({"workspace.read.all", "workspace.write.all"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: other_tenant
    try:
        assert client.get(f"/api/v1/workspaces/{workspace['id']}/threads").status_code == 404
        assert client.get(f"/api/v1/threads/{thread['id']}/events").status_code == 404
        assert client.get(f"/api/v1/runs/{run_id}/conversation").status_code == 404
        assert client.post(f"/api/v1/threads/{thread['id']}/archive").status_code == 404
        assert client.post(f"/api/v1/threads/{thread['id']}/resume").status_code == 404
        assert (
            client.post(
                f"/api/v1/threads/{thread['id']}/fork", json={"title": "Denied"}
            ).status_code
            == 404
        )
    finally:
        client.app.dependency_overrides.pop(get_principal, None)


def test_conversation_context_prefers_recent_turns_and_honors_bounds(
    app_settings: Settings,
) -> None:
    settings = app_settings.model_copy(
        update={
            "conversation_context_max_turns": 1,
            "conversation_context_max_chars": 1_000,
            "conversation_context_max_chars_per_message": 256,
        }
    )
    with TestClient(
        create_app(settings),
        headers={"Authorization": f"Bearer {settings.dev_bearer_token.get_secret_value()}"},
    ) as bounded_client:
        _, thread = _create_workspace_and_thread(bounded_client)
        first = bounded_client.post(
            f"/api/v1/threads/{thread['id']}/turns",
            json={"input": "A" * 300},
        )
        second = bounded_client.post(
            f"/api/v1/threads/{thread['id']}/turns",
            json={"input": "B" * 300},
        )
        third = bounded_client.post(
            f"/api/v1/threads/{thread['id']}/turns",
            json={"input": "Current follow-up"},
        )
        assert first.status_code == second.status_code == third.status_code == 202

        snapshots = bounded_client.get(f"/api/v1/runs/{third.json()['run']['id']}/conversation")
        assert snapshots.status_code == 200, snapshots.text
        assert len(snapshots.json()) == 1
        assert snapshots.json()[0]["source_turn_id"] == second.json()["turn"]["id"]
        assert snapshots.json()[0]["user_content"] == "B" * 256
