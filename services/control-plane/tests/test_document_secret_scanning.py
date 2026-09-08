"""文档中的 IM Secret 同样必须被发布扫描拦截；报告不含原值。"""

from pathlib import Path

import pytest

from obsion.release.hardening import scan_secrets


@pytest.mark.parametrize(
    "key",
    ["OBSION_DINGTALK_APP_SECRET", "OBSION_FEISHU_APP_SECRET", "OBSION_WECOM_CORP_SECRET"],
)
@pytest.mark.parametrize(
    "syntax", ["export {key}={value}", '{key}="{value}"', '"{key}": "{value}"']
)
def test_document_im_secrets_are_detected_without_reporting_values(
    tmp_path: Path, key: str, syntax: str
) -> None:
    value = "synthetic-secret-" + "z" * 32
    (tmp_path / "guide.md").write_text(syntax.format(key=key, value=value), encoding="utf-8")
    findings = scan_secrets(tmp_path)
    assert len(findings) == 1
    assert findings[0].path == "guide.md"
    assert findings[0].line == 1
    assert findings[0].kind == "im_application_secret"
    assert value not in repr(findings)


@pytest.mark.parametrize(
    "value",
    ["", "<由安全环境注入；历史值必须轮换>", '"${OBSION_DINGTALK_APP_SECRET}"'],
)
def test_document_secret_references_are_not_credentials(tmp_path: Path, value: str) -> None:
    (tmp_path / "guide.md").write_text(f"OBSION_DINGTALK_APP_SECRET={value}\n", encoding="utf-8")
    assert not scan_secrets(tmp_path)


def test_historical_deployment_documents_have_no_literal_im_secrets(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3]
    for name in (
        "DINGTALK_BOT_DEPLOYMENT_GUIDE.md",
        "DINGTALK_BOT_RUNNING.md",
        "DINGTALK_BOT_SUCCESS.md",
        "ENTERPRISE_INTEGRATION_REPORT.md",
    ):
        (tmp_path / name).write_text((root / name).read_text(encoding="utf-8"), encoding="utf-8")
    findings = scan_secrets(tmp_path)
    if findings:
        pytest.fail(f"历史部署文档仍有疑似凭据：{findings}", pytrace=False)
