from __future__ import annotations

import io

import pytest

from obsion_cli.config import CliError
from obsion_cli.main import _clarification_answers, build_parser, main


def test_main_requires_a_token_and_does_not_connect() -> None:
    stderr = io.StringIO()
    code = main(["workspace", "list"], stderr=stderr)
    assert code == 1
    assert "OBSION_TOKEN" in stderr.getvalue()


def test_run_answer_parser_supports_option_and_json_values() -> None:
    args = build_parser().parse_args(
        [
            "run",
            "answer",
            "run-1",
            "clarification-1",
            "--intent-revision",
            "3",
            "--option",
            "repository=option-1",
            "--value",
            'time_range={"start":"2026-09-01","end":"2026-09-02"}',
        ]
    )
    assert args.run_id == "run-1"
    assert args.clarification_id == "clarification-1"
    assert args.intent_revision == 3
    assert _clarification_answers(args.option, args.value) == [
        {"slot": "repository", "option_id": "option-1"},
        {
            "slot": "time_range",
            "value": {"start": "2026-09-01", "end": "2026-09-02"},
        },
    ]


def test_run_answer_parser_rejects_duplicate_or_missing_answers() -> None:
    with pytest.raises(CliError, match="at least one"):
        _clarification_answers([], [])
    with pytest.raises(CliError, match="more than once"):
        _clarification_answers(["repository=option-1"], ["Repository=payments"])
