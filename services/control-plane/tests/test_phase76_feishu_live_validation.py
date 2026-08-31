from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_live_validation_requires_explicit_opt_in() -> None:
    environment = dict(os.environ)
    for name in (
        "OBSION_FEISHU_LIVE",
        "OBSION_FEISHU_APP_ID",
        "OBSION_FEISHU_APP_SECRET",
    ):
        environment.pop(name, None)
    make = shutil.which("make")
    assert make is not None
    result = subprocess.run(  # noqa: S603 - fixed local Make target, no user input
        [make, "validate-feishu-live"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "OBSION_FEISHU_LIVE=1 is required" in result.stdout
    assert "app_secret" not in result.stdout.casefold()


def test_live_validation_is_bounded_to_three_non_sending_probes() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    im_tests = (REPOSITORY_ROOT / "apps/im-adapter/tests/test_feishu.py").read_text(
        encoding="utf-8"
    )
    document_tests = (
        REPOSITORY_ROOT / "services/control-plane/tests/test_phase64_feishu_knowledge.py"
    ).read_text(encoding="utf-8")
    wiki_tests = (
        REPOSITORY_ROOT / "services/control-plane/tests/test_phase65_feishu_wiki_spaces.py"
    ).read_text(encoding="utf-8")

    target = makefile.split("validate-feishu-live:", 1)[1].split("\n\n", 1)[0]
    assert "OBSION_FEISHU_LIVE=1 is required" in target
    assert "OBSION_FEISHU_APP_ID is required" in target
    assert "OBSION_FEISHU_APP_SECRET is required" in target
    assert "pytest --no-cov -m live" in target
    assert "worker.txt" not in target
    assert "send_text" not in target

    assert "@pytest.mark.live\nasync def test_feishu_live_tenant_token" in im_tests
    assert "@pytest.mark.live\nasync def test_feishu_docs_live_missing_document" in document_tests
    assert "@pytest.mark.live\nasync def test_feishu_wiki_live_list" in wiki_tests


def test_phase76_does_not_add_a_second_runtime_or_credential_file_loader() -> None:
    changed_runtime = (
        REPOSITORY_ROOT / "services/control-plane/src/obsion/capabilities/feishu_docs.py"
    ).read_text(encoding="utf-8")
    assert "DENIED_VENDOR_CODES" in changed_runtime
    assert "99992402" in changed_runtime
    assert "worker.txt" not in changed_runtime
    assert "obsion_im" not in changed_runtime
