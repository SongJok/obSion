import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from obsion.app_server.dispatcher import AppServerDispatcher
from obsion.app_server.protocol import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    PARSE_ERROR,
    ProtocolFailure,
    parse_request,
)
from obsion.security.identity import Principal


def test_json_rpc_request_parser_is_strict_and_preserves_notifications() -> None:
    request = parse_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "request-1",
                "method": "run.get",
                "params": {"run_id": "01900000-0000-7000-8000-000000000001"},
            }
        )
    )
    assert request.has_id is True
    assert request.request_id == "request-1"
    assert request.method == "run.get"

    notification = parse_request('{"jsonrpc":"2.0","method":"server.ping"}')
    assert notification.has_id is False
    assert notification.request_id is None


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("{", PARSE_ERROR),
        ("[]", INVALID_REQUEST),
        ('{"jsonrpc":"1.0","id":1,"method":"run.get"}', INVALID_REQUEST),
        ('{"jsonrpc":"2.0","id":null,"method":"run.get"}', INVALID_REQUEST),
        ('{"jsonrpc":"2.0","id":true,"method":"run.get"}', INVALID_REQUEST),
        (
            '{"jsonrpc":"2.0","id":1,"method":"run.get","params":[]}',
            INVALID_PARAMS,
        ),
        (
            '{"jsonrpc":"2.0","id":1,"method":"run.get","extra":true}',
            INVALID_REQUEST,
        ),
    ],
)
def test_json_rpc_request_parser_rejects_ambiguous_frames(raw: str, code: int) -> None:
    with pytest.raises(ProtocolFailure) as captured:
        parse_request(raw)
    assert captured.value.code == code


@pytest.mark.asyncio
async def test_clarification_answer_dispatch_is_canonical_and_idempotent() -> None:
    application = SimpleNamespace(
        answer_run_clarification=AsyncMock(
            return_value={"id": "01900000-0000-7000-8000-000000000001", "status": "RUNNING"}
        )
    )
    dispatcher = AppServerDispatcher(application)  # type: ignore[arg-type]
    principal = Principal(
        id=uuid4(),
        organization_id=uuid4(),
        external_id="phase100-client",
        display_name="Phase 100 client",
    )
    base_params = {
        "run_id": "01900000-0000-7000-8000-000000000001",
        "clarification_id": "01900000-0000-7000-8000-000000000002",
        "expected_intent_revision": 2,
    }
    answers = [
        {"slot": "time_range", "value": {"start": "2026-09-01", "end": "2026-09-02"}},
        {
            "slot": "repository",
            "option_id": "01900000-0000-7000-8000-000000000003",
        },
    ]

    fingerprints = []
    for index, ordered_answers in enumerate((answers, list(reversed(answers))), start=1):
        request = parse_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": index,
                    "method": "run.clarification.answer",
                    "params": {
                        "client_request_id": f"clarification-answer-{index}",
                        **base_params,
                        "answers": ordered_answers,
                    },
                }
            )
        )
        response = await dispatcher.dispatch(request, principal, UUID(int=index))
        assert response == {
            "jsonrpc": "2.0",
            "id": index,
            "result": {"id": base_params["run_id"], "status": "RUNNING"},
        }
        fingerprints.append(
            application.answer_run_clarification.await_args.kwargs["fingerprint_params"]
        )

    assert fingerprints[0] == fingerprints[1]
    assert [item["slot"] for item in fingerprints[0]["answers"]] == [
        "repository",
        "time_range",
    ]
    assert application.answer_run_clarification.await_args.kwargs["client_request_id"] == (
        "clarification-answer-2"
    )
