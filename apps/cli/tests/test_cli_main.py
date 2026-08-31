from __future__ import annotations

import io

from obsion_cli.main import main


def test_main_requires_a_token_and_does_not_connect() -> None:
    stderr = io.StringIO()
    code = main(["workspace", "list"], stderr=stderr)
    assert code == 1
    assert "OBSION_TOKEN" in stderr.getvalue()
