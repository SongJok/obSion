import json
from pathlib import Path

import pytest

from obsion.cli import build_parser

ROOT = Path(__file__).resolve().parents[3]


def test_project_status_cli_outputs_local_scope(capsys: pytest.CaptureFixture[str]) -> None:
    args = build_parser().parse_args(["validate-project-status", "--root", str(ROOT)])
    args.handler(args)
    result = json.loads(capsys.readouterr().out)
    assert result["scope"] == "repository-local-phase-declarations"
    assert result["production_promotion_evaluated"] is False
    assert "promotion_eligible" not in result


def test_project_status_cli_fails_for_missing_status(tmp_path: Path) -> None:
    args = build_parser().parse_args(["validate-project-status", "--root", str(tmp_path)])
    with pytest.raises(SystemExit, match="unable to load project status"):
        args.handler(args)
