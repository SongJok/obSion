from decimal import Decimal

from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.db.models import Evidence, Run, Thread, Turn
from obsion.domain.enums import (
    ArtifactKind,
    Classification,
    EvidenceType,
    RunStatus,
    ThreadStatus,
)
from obsion.harness.runtime import HarnessRuntime


def test_data_evidence_produces_sql_table_and_chart_artifacts() -> None:
    organization_id = new_id()
    user_id = new_id()
    thread = Thread(
        id=new_id(),
        organization_id=organization_id,
        workspace_id=new_id(),
        title="Revenue analysis",
        status=ThreadStatus.ACTIVE,
        created_by=user_id,
    )
    turn = Turn(
        id=new_id(),
        organization_id=organization_id,
        thread_id=thread.id,
        ordinal=1,
        created_by=user_id,
        input_text="Revenue by region",
        sanitized_input="Revenue by region",
        context_refs=[],
        attachment_refs=[],
        created_at=utc_now(),
    )
    run = Run(
        id=new_id(),
        organization_id=organization_id,
        turn_id=turn.id,
        status=RunStatus.RUNNING,
        plan={
            "route": "DATA",
            "steps": [
                {
                    "capability": "data.query",
                    "payload": {
                        "sql": "SELECT region, SUM(revenue) AS revenue FROM analytics.sales",
                        "parameters": [],
                        "parameter_types": [],
                    },
                    "resource": {
                        "table": "analytics.sales",
                        "metric": {"display_name": "Revenue"},
                        "validation": {"valid": True},
                    },
                }
            ],
        },
    )
    evidence = Evidence(
        id=new_id(),
        organization_id=organization_id,
        run_id=run.id,
        evidence_type=EvidenceType.DATA,
        source="warehouse",
        resource="analytics.sales",
        observed_at=utc_now(),
        ingested_at=utc_now(),
        content={
            "columns": ["region", "revenue"],
            "rows": [
                {"region": "East", "revenue": "42.5"},
                {"region": "West", "revenue": Decimal("31.25")},
            ],
            "row_count": 2,
        },
        content_fingerprint="a" * 64,
        confidence=Decimal("1"),
        classification=Classification.CONFIDENTIAL,
        permissions=["data.query"],
        lineage={},
    )

    runtime = object.__new__(HarnessRuntime)
    artifacts = runtime._data_result_artifacts(run, turn, thread, [evidence])

    assert [item.kind for item in artifacts] == [
        ArtifactKind.SQL,
        ArtifactKind.TABLE,
        ArtifactKind.CHART,
    ]
    assert all(item.classification == Classification.CONFIDENTIAL for item in artifacts)
    assert artifacts[0].inline_content["validation"]["valid"] is True
    assert artifacts[1].inline_content["row_count"] == 2
    chart_values = artifacts[2].inline_content["data"]["values"]
    assert chart_values[0]["revenue"] == 42.5
    assert chart_values[1]["revenue"] == 31.25
