"""Read-only fixed-commit directory discovery; never execute repository content."""

import copy

import httpx
import pytest
from jsonschema import Draft202012Validator
from test_codeup_connector import CREDENTIAL, NAME, SHA, connector, invoke
from test_codeup_gateway import configure, install_transport, read

from obsion.capabilities.codeup import read_codeup
from obsion.capabilities.codeup_contract import MAX_TREE_ENTRIES, output_schema
from obsion.common.errors import ObsionError


def entry(name="README.md", *, kind="blob", mode="100644", lfs=False, parent=""):
    return {
        "id": "b" * 40,
        "isLFS": lfs,
        "mode": mode,
        "name": name,
        "path": f"{parent}/{name}" if parent else name,
        "type": kind,
    }


def payload(path=""):
    return {"operation": "codeup.tree.list", "repository": NAME, "commit_id": SHA, "path": path}


async def test_tree_get_is_fixed_commit_and_single_directory_only():
    calls = []

    def respond(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path.endswith("/repositories/123/files/tree")
        assert dict(request.url.params) == {"ref": SHA, "path": "src", "type": "DIRECT"}
        return httpx.Response(200, json=[entry("main.go", parent="src")])

    result = await read_codeup(
        connector(),
        payload("src"),
        CREDENTIAL,
        request_timeout=5,
        transport=httpx.MockTransport(respond),
    )
    assert len(calls) == 1
    assert result["complete"] and result["next_page"] is None
    assert result["items"][0]["path"] == "src/main.go"
    assert result["items"][0]["commit_id"] == SHA
    assert result["items"][0]["can_read_content"] is True
    Draft202012Validator(output_schema("codeup.tree.list")).validate(result)


async def test_listing_retains_unreadable_entries_without_reading_their_bodies():
    data = [
        entry(),
        entry(".env"),
        entry("private.pem"),
        entry("module", kind="commit", mode="160000"),
        entry("link", mode="120000"),
        entry("large.bin", lfs=True),
        entry("src", kind="tree", mode="40000"),
    ]
    result = await invoke(payload(), httpx.Response(200, json=data))
    assert result["count"] == 7 and result["complete"]
    assert [item["name"] for item in result["items"]] == [item["name"] for item in data]
    assert [item["can_read_content"] for item in result["items"]] == [True] + [False] * 6
    assert result["items"][-1]["mode"] == "040000"
    assert all("content" not in item for item in result["items"])


@pytest.mark.parametrize(
    "path",
    [
        "../other",
        "/etc",
        ".git",
        ".ssh",
        ".env",
        "src//nested",
        "src/%2e%2e",
        "src\\nested",
        "src\nother",
    ],
)
async def test_invalid_or_restricted_directories_do_not_reach_provider(path):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=[])

    with pytest.raises(ObsionError) as error:
        await read_codeup(
            connector(),
            payload(path),
            CREDENTIAL,
            request_timeout=5,
            transport=httpx.MockTransport(respond),
        )
    assert error.value.code == "codeup_operation_invalid"
    assert not calls


@pytest.mark.parametrize(
    "change",
    [
        {"id": "not-a-git-object"},
        {"path": "other/README.md"},
        {"path": "../README.md"},
        {"name": "different"},
        {"isLFS": "false"},
        {"isLFS": None},
        {"mode": "777"},
        {"type": "tree", "mode": "100644"},
        {"type": "unknown"},
        {"type": "tree", "mode": "040000", "isLFS": True},
    ],
)
async def test_untrusted_tree_metadata_cannot_change_identity(change):
    bad = {**entry(), **change}
    with pytest.raises(ObsionError) as error:
        await invoke(payload(), httpx.Response(200, json=[bad]))
    assert error.value.code == "codeup_response_invalid"


async def test_directory_duplicates_and_oversize_fail_closed():
    data = [entry(), copy.deepcopy(entry())]
    with pytest.raises(ObsionError, match="身份或内容校验"):
        await invoke(payload(), httpx.Response(200, json=data))
    data = [entry(f"file-{i}") for i in range(MAX_TREE_ENTRIES + 1)]
    with pytest.raises(ObsionError):
        await invoke(payload(), httpx.Response(200, json=data))


async def test_empty_directory_and_exact_cap_are_not_confused():
    empty = await invoke(payload(), httpx.Response(200, json=[]))
    assert empty["count"] == 0 and empty["complete"]
    data = [entry(f"file-{i}") for i in range(MAX_TREE_ENTRIES)]
    capped = await invoke(payload(), httpx.Response(200, json=data))
    assert capped["count"] == MAX_TREE_ENTRIES
    assert not capped["complete"] and capped["next_page"] is None
    Draft202012Validator(output_schema("codeup.tree.list")).validate(capped)


@pytest.mark.parametrize(
    "extra", [{"type": "RECURSIVE"}, {"ref": "master"}, {"commit_id": "master"}]
)
async def test_mutable_refs_and_unbounded_recursion_are_rejected(extra):
    with pytest.raises(ObsionError) as error:
        await invoke({**payload(), **extra}, httpx.Response(200, json=[]))
    assert error.value.code == "codeup_operation_invalid"


def test_tree_rest_uses_existing_gateway_policy_and_audit(client, monkeypatch):
    repository_id = configure(client, monkeypatch)
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=[entry()])

    install_transport(client, respond)
    response = read(client, repository_id, {"operation": "codeup.tree.list", "commit_id": SHA})
    assert response.status_code == 200, response.text
    assert response.json()["policy_decision_id"]
    assert response.json()["items"][0]["object_id"] == "b" * 40
    assert len(calls) == 1 and calls[0].method == "GET"
    denied = read(client, repository_id, {"operation": "codeup.tree.list", "commit_id": "master"})
    assert denied.status_code == 422
    assert len(calls) == 1
