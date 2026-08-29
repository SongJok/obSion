from test_phase14_semantic_layer import _create_catalog, _wait_terminal


def test_dataagent_keeps_decline_analysis_on_governed_data_dimensions(client) -> None:
    catalog = _create_catalog(client)
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Phase 16 DataAgent", "description": "Governed analytics"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Why did paid users decline?"},
    )
    assert thread.status_code == 201, thread.text
    created = client.post(
        "/api/v1/data/query",
        json={"thread_id": thread.json()["id"], "question": "为什么付费人数下降"},
    )
    assert created.status_code == 202, created.text

    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["intent"]["route"] == "DATA"
    assert run["intent"]["agent"] == "data-agent"
    assert run["intent"]["skill"] == "governed-analytics"
    assert run["plan"]["agent"] == "data-agent"
    assert run["plan"]["skill"]["name"] == "governed-analytics"
    assert run["plan"]["skill"]["required_evidence"] == ["DATA"]
    capabilities = [step["capability"] for step in run["plan"]["steps"] if "capability" in step]
    assert capabilities == ["data.query"]
    assert "log.search" not in capabilities
    assert "trace.search" not in capabilities
    assert run["plan"]["verification"] == [
        "metric_definition",
        "sql_validated",
        "result_cited",
    ]
    assert run["intent"]["metrics"][0]["id"] == catalog["metric_id"]
