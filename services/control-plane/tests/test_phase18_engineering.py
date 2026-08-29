from uuid import uuid4

import httpx
import pytest

from obsion.capabilities.connectors import ConnectorContext, HttpJsonExecutor
from obsion.capabilities.engineering import normalize_response
from obsion.capabilities.gateway import CapabilityGateway, GatewayRequest
from obsion.common.errors import ValidationError
from obsion.config import Environment, Settings
from obsion.db.models import Connector
from obsion.domain.enums import ConnectorStatus
from obsion.security.identity import Principal


def test_git_diff_normalizes_commit_lineage_and_redacts_patch_content() -> None:
    normalized = normalize_response(
        {
            "commits": [
                {
                    "committed_at": "2026-08-29T01:00:00Z",
                    "repo": "obsion/control-plane",
                    "sha": "abcdef1234567",
                    "deployment_id": "deploy-42",
                    "message": "rotate password=super-secret",
                    "patch": "- timeout=30\n+ timeout=20",
                    "files": ["services/api.py"],
                }
            ]
        },
        operation="git.diff",
        default_repository="*",
        default_environment="production",
    )
    item = normalized["items"][0]
    assert item["repository"] == "obsion/control-plane"
    assert item["commit_id"] == "abcdef1234567"
    assert item["deployment_id"] == "deploy-42"
    assert item["attributes"]["message"] == "rotate password=[REDACTED]"
    assert item["attributes"]["files"] == ["services/api.py"]


def _connector(*, allowed_repositories: list[str] | None = None) -> Connector:
    return Connector(
        id=uuid4(),
        organization_id=uuid4(),
        name="engineering-test",
        connector_type="engineering-http",
        status=ConnectorStatus.ACTIVE,
        environment="development",
        endpoint="http://engineering.test/query",
        configuration={
            "protocol": "engineering.v1",
            **(
                {"allowed_repositories": allowed_repositories}
                if allowed_repositories is not None
                else {}
            ),
        },
        declared_grants=["code.read", "deployment.read"],
        allowed_egress=["engineering.test:80"],
    )


def _context(connector: Connector) -> ConnectorContext:
    return ConnectorContext(
        principal=Principal(
            id=uuid4(),
            organization_id=connector.organization_id,
            external_id="phase18-user",
            display_name="Phase 18 User",
        ),
        run_id=uuid4(),
        step_id=None,
    )


@pytest.mark.asyncio
async def test_engineering_http_executor_normalizes_deployment_commit_lineage() -> None:
    async def responder(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/query"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "timestamp": "2026-08-29T01:00:00Z",
                        "repository": "obsion/control-plane",
                        "deployment_id": "deploy-42",
                        "commit_id": "abcdef1234567",
                        "service": "api",
                        "status": "healthy",
                    }
                ]
            },
        )

    connector = _connector()
    executor = HttpJsonExecutor(
        Settings(environment=Environment.TEST), transport=httpx.MockTransport(responder)
    )
    result = await executor.invoke(
        connector,
        {
            "operation": "deployment.commit",
            "repository": "obsion/control-plane",
            "deployment_id": "deploy-42",
        },
        None,
        _context(connector),
    )
    assert result.data["operation"] == "deployment.commit"
    assert result.data["items"][0]["commit_id"] == "abcdef1234567"


@pytest.mark.asyncio
async def test_engineering_connector_denies_repository_outside_allowlist() -> None:
    connector = _connector(allowed_repositories=["obsion/control-plane"])
    executor = HttpJsonExecutor(Settings(environment=Environment.TEST))
    with pytest.raises(ValidationError) as caught:
        await executor.invoke(
            connector,
            {
                "operation": "git.commit",
                "repository": "untrusted/other",
                "commit_id": "abcdef1234567",
            },
            None,
            _context(connector),
        )
    assert caught.value.code == "engineering_repository_denied"
    request = GatewayRequest(
        principal=_context(connector).principal,
        capability_name="git.commit",
        payload={"repository": "untrusted/other"},
        resource={"repository": "untrusted/other"},
        environment="development",
        agent_name="engineering-agent",
        run_id=uuid4(),
    )
    assert CapabilityGateway._engineering_repository_denied(connector, request)  # noqa: SLF001
