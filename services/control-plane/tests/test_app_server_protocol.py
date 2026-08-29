import json

import pytest

from obsion.app_server.protocol import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    PARSE_ERROR,
    ProtocolFailure,
    parse_request,
)


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
