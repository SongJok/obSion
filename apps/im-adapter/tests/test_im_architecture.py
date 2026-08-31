from __future__ import annotations

import ast
from pathlib import Path

IM_ROOT = Path(__file__).resolve().parents[1] / "src" / "obsion_im"
FORBIDDEN_PREFIXES = (
    "obsion.harness",
    "obsion.db",
    "obsion.capabilities",
    "obsion.model_gateway",
    "obsion.persistence",
    "obsion.api",
    "sqlalchemy",
    "fastapi",
    "lark_oapi",
    "dingtalk_sdk",
    "alibabacloud_dingtalk",
    "wechatpy",
    "httpx",
    "aiohttp",
    "requests",
    "urllib",
    "http.client",
)
HTTP_VENDOR_MODULES = frozenset({"feishu.py", "dingtalk.py", "wecom.py"})
VENDOR_ENDPOINTS = {
    "feishu.py": "open.feishu.cn",
    "dingtalk.py": "oapi.dingtalk.com",
    "wecom.py": "qyapi.weixin.qq.com",
}


def _imports(tree: ast.AST) -> list[tuple[str, int]]:
    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append((node.module, node.lineno))
    return imports


def test_im_adapter_is_an_experience_client_not_a_second_harness() -> None:
    violations: list[str] = []
    for path in sorted(IM_ROOT.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        for imported, line in _imports(tree):
            if imported == "obsion" or imported.startswith("obsion."):
                violations.append(f"{path.name}:{line} imports control-plane module {imported}")
            if (imported.startswith(FORBIDDEN_PREFIXES) or imported in FORBIDDEN_PREFIXES) and not (
                (path.name in HTTP_VENDOR_MODULES and imported == "httpx")
                or (path.name == "signatures.py" and imported.startswith("cryptography"))
            ):
                violations.append(f"{path.name}:{line} imports {imported}")
        for module_name, endpoint in VENDOR_ENDPOINTS.items():
            if endpoint in text and path.name != module_name:
                violations.append(f"{path.name} contains vendor endpoint {endpoint}")
        if path.name in HTTP_VENDOR_MODULES:
            expected = VENDOR_ENDPOINTS[path.name]
            if expected not in text:
                violations.append(f"{path.name} must pin {expected}")
            for other_endpoint in VENDOR_ENDPOINTS.values():
                if other_endpoint != expected and other_endpoint in text:
                    violations.append(f"{path.name} must not contain {other_endpoint}")
    assert violations == [], "IM adapter crossed the Experience client boundary:\n" + "\n".join(
        violations
    )


def test_outbound_replies_are_local_or_explicit_vendor_http_envelopes() -> None:
    replies = (IM_ROOT / "replies.py").read_text(encoding="utf-8")
    config = (IM_ROOT / "config.py").read_text(encoding="utf-8")
    webhook = (IM_ROOT / "webhook.py").read_text(encoding="utf-8")
    assert "local_outbox" in replies
    assert "Generic HTTP delivery is not implemented" in config
    assert "dingtalk-http" in config
    assert "wecom-http" in config
    assert "httpx" not in replies
    feishu = (IM_ROOT / "feishu.py").read_text(encoding="utf-8")
    assert "tenant_access_token/internal/" in feishu
    assert 'FEISHU_ORIGIN = "https://open.feishu.cn"' in feishu
    dingtalk = (IM_ROOT / "dingtalk.py").read_text(encoding="utf-8")
    assert 'DINGTALK_ORIGIN = "https://oapi.dingtalk.com"' in dingtalk
    assert 'TOKEN_PATH = "/gettoken"' in dingtalk
    wecom = (IM_ROOT / "wecom.py").read_text(encoding="utf-8")
    assert 'WECOM_ORIGIN = "https://qyapi.weixin.qq.com"' in wecom
    assert "cgi-bin/gettoken" in wecom
    assert "requests" not in replies
    assert "127.0.0.1" in webhook
    assert "may only bind 127.0.0.1 unless --public is set" in webhook
    assert "OBSION_IM_TLS_CERT" in webhook


def test_im_bridge_delegates_principal_mapping_to_the_control_plane() -> None:
    text = (IM_ROOT / "bridge.py").read_text(encoding="utf-8")
    assert "create_im_message" in text
    assert "runtime.ask(" not in text
    assert "turn.create" not in text
