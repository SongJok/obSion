from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[3]
_MODULE_PATH = _ROOT / "dingtalk_obsion_agent.py"
_SPEC = importlib.util.spec_from_file_location("dingtalk_obsion_agent_test", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_dws_entrypoint_returns_control_plane_answer(monkeypatch, capsys) -> None:
    async def answer(question: str) -> str:
        assert question == "查询订单"
        return "来自 Harness 的回答"

    monkeypatch.setattr(_MODULE, "_ask_control_plane", answer)
    monkeypatch.setattr(sys, "argv", [str(_MODULE_PATH), "@点仔", "查询订单"])

    assert _MODULE.main() == 0
    captured = capsys.readouterr()
    assert captured.out == "来自 Harness 的回答\n"
    assert captured.err == ""


def test_dws_entrypoint_fails_closed_without_echoing_internal_error(monkeypatch, capsys) -> None:
    async def failure(question: str) -> str:
        del question
        raise RuntimeError("connector-token-secret")

    monkeypatch.setattr(_MODULE, "_ask_control_plane", failure)
    monkeypatch.setattr(sys, "argv", [str(_MODULE_PATH), "问题"])

    assert _MODULE.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "connector-token-secret" not in captured.err
    assert "控制面暂时无法处理" in captured.err


def test_dws_entrypoint_requires_a_question(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", [str(_MODULE_PATH), "@点仔"])
    assert _MODULE.main() == 2
    assert "不能为空" in capsys.readouterr().err
