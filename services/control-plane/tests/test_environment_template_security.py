"""开源环境模板不能携带操作者凭证或租户绑定。"""

from pathlib import Path

import pytest
from dotenv import dotenv_values

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_TEMPLATE = _REPOSITORY_ROOT / ".env.example"


@pytest.mark.parametrize(
    "key",
    [
        "OBSION_AI_API_KEY",
        "OBSION_AI_BASE_URL",
        "OBSION_AI_MODEL",
        "OBSION_CODEUP_APP_ID",
        "OBSION_CODEUP_ORG_ID",
        "OBSION_DINGTALK_APP_KEY",
        "OBSION_DINGTALK_APP_SECRET",
        "OBSION_FEISHU_APP_ID",
        "OBSION_FEISHU_APP_SECRET",
        "OBSION_FEISHU_ENCRYPT_KEY",
        "OBSION_FEISHU_VERIFICATION_TOKEN",
        "OBSION_FEISHU_LIVE_CHAT_ID",
        "OBSION_CONFLUENCE_EMAIL",
        "OBSION_CONFLUENCE_API_TOKEN",
        "OBSION_WECOM_CORP_ID",
        "OBSION_WECOM_CORP_SECRET",
        "OBSION_WECOM_AGENT_ID",
        "OBSION_WECOM_TOKEN",
        "OBSION_WECOM_ENCODING_AES_KEY",
        "OBSION_SECRET_ENCRYPTION_KEY",
        "OBSION_IM_WEBHOOK_SECRET",
        "OBSION_OTEL_EXPORTER_HEADERS",
    ],
)
def test_operator_configuration_is_empty_in_public_template(key: str) -> None:
    # 只报告字段名，断言失败也不能把凭证值带入测试输出。
    values = dotenv_values(_TEMPLATE, interpolate=False)
    assert key in values, f"模板缺少配置字段：{key}"
    if values[key] != "":
        pytest.fail(f"公开模板必须清空操作者配置：{key}", pytrace=False)


def test_public_template_does_not_allow_a_private_operator_model_host() -> None:
    values = dotenv_values(_TEMPLATE, interpolate=False)
    if values.get("OBSION_MODEL_ALLOWED_HOSTS") != "[]":
        pytest.fail("公开模板的模型域名白名单必须默认为空", pytrace=False)


def test_environment_template_has_no_duplicate_assignments() -> None:
    keys = [
        line.partition("=")[0].strip()
        for line in _TEMPLATE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    ]
    assert len(keys) == len(set(keys)), "环境模板存在重复配置字段"
