from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from test_phase62_feishu_http import _completed_feishu_run

from obsion.config import Settings
from obsion.security.auth import get_principal
from obsion.security.identity import Principal


def test_pending_prepare_is_not_a_resend_permission(client: TestClient) -> None:
    run_id = _completed_feishu_run(client)
    first = client.post(f"/api/v1/experience/im/runs/{run_id}/deliveries")
    assert first.status_code == 200, first.text
    second = client.post(f"/api/v1/experience/im/runs/{run_id}/deliveries")
    assert second.status_code == 409, second.text
    assert second.json()["code"] == "im_delivery_receipt_conflict"


def test_concurrent_prepare_grants_one_sender(client: TestClient) -> None:
    run_id = _completed_feishu_run(client)
    path = f"/api/v1/experience/im/runs/{run_id}/deliveries"
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: client.post(path), range(16)))
    assert [response.status_code for response in responses].count(200) == 1
    assert [response.status_code for response in responses].count(409) == 15


def test_unknown_receipt_reconciliation_and_sent_monotonicity(client: TestClient) -> None:
    run_id = _completed_feishu_run(client)
    prepared = client.post(f"/api/v1/experience/im/runs/{run_id}/deliveries").json()
    path = f"/api/v1/experience/im/deliveries/{prepared['id']}"
    failed = client.post(f"{path}/fail", json={"failure_code": "vendor_request_failed"})
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "UNKNOWN"
    for _ in range(2):
        completed = client.post(f"{path}/complete", json={"vendor_message_id": "vendor-real-1"})
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "SENT"
    conflict = client.post(f"{path}/complete", json={"vendor_message_id": "vendor-other"})
    assert conflict.status_code == 409
    late_failure = client.post(f"{path}/fail", json={"failure_code": "delivery_audit_failed"})
    assert late_failure.status_code == 200
    assert late_failure.json()["status"] == "SENT"
    assert late_failure.json()["vendor_message_id"] == "vendor-real-1"
    assert late_failure.json()["attempt_count"] == 1


@pytest.mark.parametrize("receipt", ["   ", "local-id"])
def test_blank_and_local_receipts_are_rejected(client: TestClient, receipt: str) -> None:
    run_id = _completed_feishu_run(client)
    prepared = client.post(f"/api/v1/experience/im/runs/{run_id}/deliveries").json()
    response = client.post(
        f"/api/v1/experience/im/deliveries/{prepared['id']}/complete",
        json={"vendor_message_id": prepared["id"] if receipt == "local-id" else receipt},
    )
    assert response.status_code == 409, response.text


def test_other_adapter_cannot_change_receipt(client: TestClient, app_settings: Settings) -> None:
    run_id = _completed_feishu_run(client)
    prepared = client.post(f"/api/v1/experience/im/runs/{run_id}/deliveries").json()
    other = Principal(
        id=UUID("00000000-0000-7000-8000-000000000098"),
        organization_id=app_settings.dev_organization_id,
        external_id="other-adapter",
        display_name="Other adapter",
        permissions=frozenset({"im.reply.deliver"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: other
    try:
        for action, body in (
            ("fail", {"failure_code": "vendor_request_failed"}),
            ("complete", {"vendor_message_id": "unowned-receipt"}),
        ):
            response = client.post(
                f"/api/v1/experience/im/deliveries/{prepared['id']}/{action}", json=body
            )
            assert response.status_code == 403, response.text
    finally:
        client.app.dependency_overrides.pop(get_principal, None)
