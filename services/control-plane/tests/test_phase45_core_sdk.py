from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_JAVA_SDK = _REPOSITORY_ROOT / "packages" / "sdk-java"
_PYTHON_CLIENT = _REPOSITORY_ROOT / "packages" / "sdk-python" / "src" / "obsion_sdk" / "client.py"
_TS_CLIENT = _REPOSITORY_ROOT / "packages" / "sdk-ts" / "src" / "index.ts"
_FORBIDDEN_JAVA_TOKENS = (
    "org.springframework",
    "springframework",
    "jakarta.servlet",
    "javax.servlet",
    "io.grpc",
    "org.apache.kafka",
    "clickhouse",
    "ProcessBuilder",
    "Runtime.getRuntime",
    "obsion.harness",
    "obsion.db",
    "WebSocket",
    "@SpringBootApplication",
    "@RestController",
)


def _java_sources() -> list[Path]:
    return sorted((_JAVA_SDK / "src" / "main" / "java").rglob("*.java"))


def test_java_sdk_is_a_rest_client_not_a_second_control_plane() -> None:
    sources = _java_sources()
    assert sources, "packages/sdk-java main sources are missing"
    violations: list[str] = []
    joined_methods = ""
    for path in sources:
        text = path.read_text(encoding="utf-8")
        joined_methods += text
        for needle in _FORBIDDEN_JAVA_TOKENS:
            if needle in text:
                violations.append(f"{path.relative_to(_JAVA_SDK)} contains {needle}")
    assert "class ObsionClient" in joined_methods
    for method in (
        "listWorkspaces",
        "createWorkspace",
        "createThread",
        "createTurn",
        "publishStudioAgent",
        "publishStudioSkill",
        "listConnectors",
        "createConnector",
        "listAdminCapabilities",
        "bindCapability",
        "listCapabilities",
        "invokeCapability",
    ):
        assert f" {method}(" in joined_methods, method
    pom = (_JAVA_SDK / "pom.xml").read_text(encoding="utf-8")
    assert "<maven.compiler.release>21</maven.compiler.release>" in pom
    assert "<artifactId>obsion-sdk</artifactId>" in pom
    assert violations == [], "Java SDK crossed the client boundary:\n" + "\n".join(violations)


def test_python_and_typescript_sdks_create_connectors_and_bind_capabilities() -> None:
    python_client = _PYTHON_CLIENT.read_text(encoding="utf-8")
    typescript_client = _TS_CLIENT.read_text(encoding="utf-8")
    for token in (
        'await self._request("GET", "/api/v1/admin/connectors")',
        'await self._request("POST", "/api/v1/admin/connectors"',
        'await self._request("GET", "/api/v1/admin/capabilities")',
        "/api/v1/admin/capabilities/{capability_id}/bindings",
        "/api/v1/admin/connectors/{connector_id}/health",
        "/api/v1/admin/connectors/{connector_id}/discover",
    ):
        assert token in python_client, token
    for token in (
        'this.request("/api/v1/admin/connectors")',
        'this.request("/api/v1/admin/connectors",',
        'this.request("/api/v1/admin/capabilities")',
        "/api/v1/admin/capabilities/${capabilityId}/bindings",
        "/api/v1/admin/connectors/${connectorId}/health",
        "/api/v1/admin/connectors/${connectorId}/discover",
    ):
        assert token in typescript_client, token


def _maven_command() -> list[str] | None:
    wrapper = _JAVA_SDK / "mvnw"
    if wrapper.exists():
        return [str(wrapper), "-B", "test"]
    maven = shutil.which("mvn")
    if maven:
        return [maven, "-B", "test"]
    return None


def _can_run_java_sdk_tests() -> bool:
    if _maven_command() is None:
        return False
    java_home = os.environ.get("JAVA_HOME", "")
    if java_home and Path(java_home, "bin", "java").is_file():
        return True
    return shutil.which("java") is not None


@pytest.mark.skipif(not _can_run_java_sdk_tests(), reason="JDK/Maven toolchain is not installed")
def test_java_sdk_maven_tests() -> None:
    command = _maven_command()
    assert command is not None
    env = os.environ.copy()
    java_home = env.get("JAVA_HOME")
    if not java_home:
        homebrew = Path("/opt/homebrew/opt/openjdk")
        if (homebrew / "bin" / "java").exists():
            env["JAVA_HOME"] = str(homebrew)
    completed = subprocess.run(  # noqa: S603 -- fixed Maven command; no untrusted input
        command,
        cwd=_JAVA_SDK,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
