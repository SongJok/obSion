from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from obsion.capabilities.yunxiao import YunxiaoClient
from obsion.db.models import Connector
from obsion.domain.enums import ConnectorStatus


@pytest.mark.yunxiao_live
@pytest.mark.asyncio
async def test_yunxiao_live_lists_bounded_pat_visible_repositories() -> None:
    """Exercise only the read-only repository contract against an operator tenant."""

    if os.environ.get("OBSION_YUNXIAO_LIVE") != "1":
        pytest.skip("OBSION_YUNXIAO_LIVE=1 is required")
    token = os.environ.get("OBSION_YUNXIAO_PAT", "").strip()
    if not token:
        pytest.skip("OBSION_YUNXIAO_PAT is required")

    endpoint = os.environ.get("OBSION_YUNXIAO_ENDPOINT", "https://openapi-rdc.aliyuncs.com").strip()
    if not endpoint:
        pytest.fail("OBSION_YUNXIAO_ENDPOINT must be a non-empty HTTPS origin")

    raw_organization_ids = os.environ.get("OBSION_YUNXIAO_ORGANIZATION_IDS", "")
    organization_ids = [item.strip() for item in raw_organization_ids.split(",") if item.strip()]
    organization_id = uuid4()
    connector = Connector(
        id=uuid4(),
        organization_id=organization_id,
        name="yunxiao-live-validation",
        connector_type="yunxiao",
        status=ConnectorStatus.ACTIVE,
        environment="staging",
        endpoint=endpoint,
        configuration={
            "protocol": "yunxiao.devops.v1",
            "rate_limit_per_minute": 1,
            "timeout_seconds": 15,
            **({"organization_ids": organization_ids} if organization_ids else {}),
        },
        credential_ref="env://OBSION_YUNXIAO_PAT",
        declared_grants=["code.read"],
        allowed_egress=[endpoint],
    )
    result = await YunxiaoClient(
        connector,
        token,
        timeout_seconds=15,
    ).list_repositories(page=1, per_page=10)

    assert result["operation"] == "yunxiao.repositories.list"
    assert result["page"] == 1
    assert result["per_page"] == 10
    assert result["count"] == len(result["items"])
    assert result["count"] <= 10
    assert result["total"] >= result["count"]
    assert result["next_page"] is None or result["next_page"] == 2
    for item in result["items"]:
        assert isinstance(item["organization_id"], str)
        assert isinstance(item["id"], str)
        assert isinstance(item["name"], str)
    assert token not in repr(result)


def test_yunxiao_live_target_is_opt_in_and_does_not_print_credentials() -> None:
    root = Path(__file__).resolve().parents[3]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("validate-yunxiao-live:", 1)[1].split("\n\n", 1)[0]
    assert "OBSION_YUNXIAO_LIVE=1 is required" in target
    assert "OBSION_YUNXIAO_PAT is required" in target
    assert "--no-cov -m yunxiao_live" in target
    assert 'echo "$${OBSION_YUNXIAO_PAT' not in target
    assert "worker.txt" not in target
