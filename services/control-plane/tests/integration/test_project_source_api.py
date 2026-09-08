"""真实 ASGI/认证/Policy/事务测试；不启动 worker、不联网或解析凭据。"""

import json
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event, func, select, update

from obsion.api.project_sources import get_project_source_service
from obsion.application.project_sources import ProjectSourceService
from obsion.common.time import utc_now
from obsion.config import Environment, Settings
from obsion.db.base import Base
from obsion.db.models import (
    AuditRecord,
    CodeRepository,
    CodeRepositoryGrant,
    Connector,
    Organization,
    Policy,
    PolicyDecision,
    Role,
    User,
    UserRole,
    Workspace,
)
from obsion.db.project_source_models import (
    ConnectorConfigurationVersion,
    ConnectorVersionRevocation,
    ProjectSource,
    ProjectSourceRevocation,
)
from obsion.db.session import Database
from obsion.main import create_app
from obsion.persistence.auth_sessions import AuthSessionStore
from obsion.security.auth import get_session

_PREFIX = "/api/v1/admin/project-sources"
_ACTIONS = [
    "project_source.read",
    "project_source.version.create",
    "project_source.register",
    "project_source.version.revoke",
    "project_source.revoke",
]
_MODELS = (
    ConnectorConfigurationVersion,
    ProjectSource,
    ConnectorVersionRevocation,
    ProjectSourceRevocation,
    PolicyDecision,
    AuditRecord,
)


@pytest_asyncio.fixture(params=["sqlite", "postgresql"])
async def api(request, tmp_path):
    if request.param == "postgresql":
        if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
            pytest.skip("来源 REST PostgreSQL 事务验证须显式启用")
        url = os.getenv("OBSION_DATABASE_URL", "")
        assert url.startswith("postgresql+asyncpg://")
    else:
        url = f"sqlite+aiosqlite:///{tmp_path / 'source-api.db'}"
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=url,
        allowed_origins=["http://testserver"],
        otel_enabled=False,
    )
    database = Database(settings)
    engine = database.engine
    sessions = database.sessions
    if request.param == "sqlite":
        # 仅强化测试的 FK 检查；事务配置必须来自运行时 Database。
        @event.listens_for(engine.sync_engine, "connect")
        def configure_sqlite(connection, record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    try:
        if request.param == "sqlite":
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session, session.begin():
            organization = Organization(slug=f"source-api-{uuid4()}", name="合成组织")
            session.add(organization)
            await session.flush()
            user = User(
                organization_id=organization.id,
                external_id="operator",
                display_name="合成操作方",
                email="operator@example.invalid",
            )
            role = Role(
                organization_id=organization.id,
                name="source-api-operator",
                permissions=[*_ACTIONS, "code.read.internal"],
            )
            session.add_all([user, role])
            await session.flush()
            session.add(UserRole(organization_id=organization.id, user_id=user.id, role_id=role.id))
            connector = Connector(
                organization_id=organization.id,
                name="synthetic-git",
                connector_type="git-http",
                environment="test",
                status="ACTIVE",
                endpoint="https://source.example.invalid",
                configuration={"allowed_repositories": ["synthetic/42", "synthetic/43"]},
                allowed_egress=["source.example.invalid"],
                declared_grants=["code.read"],
            )
            workspace = Workspace(
                organization_id=organization.id,
                owner_id=user.id,
                name="合成项目",
                visibility="PRIVATE",
            )
            repository = CodeRepository(
                organization_id=organization.id,
                name="合成仓库",
                classification="INTERNAL",
                acl={},
            )
            policy = Policy(
                organization_id=organization.id,
                name="synthetic-source-api",
                version=1,
                effect="ALLOW",
                conditions={"actions": _ACTIONS},
                obligations=[],
                reason="仅合成测试",
                created_by=user.id,
            )
            session.add_all([connector, workspace, repository, policy])
            issued = await AuthSessionStore().create(
                session, organization_id=organization.id, user_id=user.id, ttl_seconds=600
            )
        app = create_app(settings)
        app.state.settings = settings
        app.state.database = database
        # 显式不启动 lifespan/worker；路由、认证、服务、Policy/Audit 都使用真实实现。
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://testserver",
            cookies={"obsion_session": issued.token},
            headers={"Origin": "http://testserver", "X-Request-ID": "synthetic-source-api"},
        ) as client:
            yield SimpleNamespace(
                client=client,
                app=app,
                sessions=sessions,
                organization=organization,
                user=user,
                role=role,
                policy=policy,
                connector=connector,
                workspace=workspace,
                repository=repository,
                token=issued.token,
                dialect=request.param,
            )
    finally:
        await engine.dispose()


async def _counts(api):
    async with api.sessions() as session:
        return [
            await session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.organization_id == api.organization.id)
            )
            for model in _MODELS
        ]


async def _change(api, model, identifier, **values):
    async with api.sessions() as session, session.begin():
        await session.execute(update(model).where(model.id == identifier).values(**values))


async def _version(api):
    response = await api.client.post(
        _PREFIX + "/versions", json={"connector_id": str(api.connector.id)}
    )
    assert response.status_code == 201, response.text
    return response.json()["record_id"]


def _binding(api, version):
    return {
        "workspace_id": str(api.workspace.id),
        "repository_id": str(api.repository.id),
        "connector_version_id": version,
        "provider_repository_id": "synthetic/42",
    }


async def _operation(api, action):
    if action == "version":
        return _PREFIX + "/versions", {"connector_id": str(api.connector.id)}
    version = await _version(api)
    if action == "register":
        return _PREFIX, _binding(api, version)
    if action == "version-revoke":
        return _PREFIX + f"/versions/{version}/revoke", {"reason": "OPERATOR_REVOKED"}
    response = await api.client.post(_PREFIX, json=_binding(api, version))
    assert response.status_code == 200, response.text
    return _PREFIX + f"/{response.json()['record_id']}/revoke", {"reason": "OPERATOR_REVOKED"}


def _error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert set(response.json()) == {"code", "message", "correlation_id", "details"}
    assert response.json()["correlation_id"] == response.headers["x-request-id"]
    assert "synthetic/42" not in response.text
    assert "source.example.invalid" not in response.text


async def test_rest_persists_success_replay_conflict_and_both_revocations(api):
    version = await _version(api)
    binding = _binding(api, version)
    response = await api.client.post(_PREFIX, json=binding)
    assert response.status_code == 200, response.text
    source = response.json()["record_id"]
    assert set(response.json()) == {"outcome", "record_id", "policy_decision_id", "reason"}
    replay = await api.client.post(_PREFIX, json=binding)
    assert replay.status_code == 200 and replay.json()["outcome"] == "UNCHANGED"
    assert replay.json()["record_id"] == source
    assert replay.json()["policy_decision_id"] != response.json()["policy_decision_id"]
    conflict = await api.client.post(
        _PREFIX, json={**binding, "provider_repository_id": "synthetic/43"}
    )
    _error(conflict, 409, "project_source_conflict")
    for path in (f"/{source}/revoke", f"/versions/{version}/revoke"):
        revoked = await api.client.post(_PREFIX + path, json={"reason": "OPERATOR_REVOKED"})
        assert revoked.status_code == 200 and revoked.json()["outcome"] == "CREATED"
        repeated = await api.client.post(_PREFIX + path, json={"reason": "SECURITY_REVOKED"})
        assert repeated.status_code == 200 and repeated.json()["outcome"] == "UNCHANGED"
    denied = await api.client.post(_PREFIX, json=binding)
    _error(denied, 403, "project_source_denied")
    assert await _counts(api) == [1, 1, 1, 1, 9, 9]
    async with api.sessions() as session:
        audits = list(
            await session.scalars(
                select(AuditRecord).where(AuditRecord.organization_id == api.organization.id)
            )
        )
        assert {audit.actor_id for audit in audits} == {api.user.id}
        assert all(audit.policy_decision_id is not None for audit in audits)
        assert {audit.outcome for audit in audits} == {"CREATED", "UNCHANGED", "CONFLICT", "DENIED"}
        for model in (ConnectorVersionRevocation, ProjectSourceRevocation):
            record = await session.scalar(
                select(model).where(model.organization_id == api.organization.id)
            )
            assert record.reason_code == "OPERATOR_REVOKED"
            assert record.revoked_by == api.user.id
        payload = json.dumps([audit.redacted_metadata for audit in audits])
        assert "source.example.invalid" not in payload and "synthetic/42" not in payload
        assert "configuration" not in payload and "credential" not in payload


async def test_inventory_lists_active_and_revoked_bindings_without_connector_secrets(api):
    version = await _version(api)
    response = await api.client.post(_PREFIX, json=_binding(api, version))
    assert response.status_code == 200, response.text
    source_id = response.json()["record_id"]

    versions = await api.client.get(_PREFIX + "/versions")
    assert versions.status_code == 200, versions.text
    assert len(versions.json()) == 1
    version_view = versions.json()[0]
    assert version_view["id"] == version
    assert version_view["connector_name"] == "synthetic-git"
    assert version_view["active"] is True
    assert "configuration" not in version_view
    assert "credential_ref" not in version_view
    assert "endpoint" not in version_view

    sources = await api.client.get(_PREFIX)
    assert sources.status_code == 200, sources.text
    assert sources.json()[0]["id"] == source_id
    assert sources.json()[0]["provider_repository_id"] == "synthetic/42"
    assert sources.json()[0]["active"] is True
    assert "configuration" not in sources.json()[0]
    assert "credential_ref" not in sources.json()[0]

    revoked = await api.client.post(
        _PREFIX + f"/{source_id}/revoke", json={"reason": "OPERATOR_REVOKED"}
    )
    assert revoked.status_code == 200, revoked.text
    assert (await api.client.get(_PREFIX)).json() == []
    include_revoked = await api.client.get(_PREFIX, params={"include_revoked": "true"})
    assert include_revoked.status_code == 200, include_revoked.text
    view = include_revoked.json()[0]
    assert view["active"] is False
    assert view["source_revocation_reason"] == "OPERATOR_REVOKED"

    version_revoked = await api.client.post(
        _PREFIX + f"/versions/{version}/revoke", json={"reason": "SECURITY_REVOKED"}
    )
    assert version_revoked.status_code == 200, version_revoked.text
    revoked_versions = await api.client.get(
        _PREFIX + "/versions", params={"include_revoked": "true"}
    )
    assert revoked_versions.status_code == 200, revoked_versions.text
    assert revoked_versions.json()[0]["active"] is False
    assert revoked_versions.json()[0]["revocation_reason"] == "SECURITY_REVOKED"


async def test_inventory_filters_by_workspace_and_repository(api):
    version = await _version(api)
    await api.client.post(_PREFIX, json=_binding(api, version))
    assert (await api.client.get(_PREFIX, params={"workspace_id": str(api.workspace.id)})).json()
    assert (await api.client.get(_PREFIX, params={"repository_id": str(api.repository.id)})).json()
    assert (await api.client.get(_PREFIX, params={"workspace_id": str(uuid4())})).json() == []


async def test_inventory_requires_explicit_read_permission(api):
    await _change(api, Role, api.role.id, permissions=[])
    for path in (_PREFIX, _PREFIX + "/versions"):
        response = await api.client.get(path)
        _error(response, 403, "admin_access_denied")


@pytest.mark.parametrize("action", ["version", "register", "version-revoke", "source-revoke"])
@pytest.mark.parametrize(
    "gate", ["permission", "im-delegate", "policy", "default-mask", "obligation"]
)
async def test_business_denial_commits_audit_before_error_response(api, action, gate):
    path, body = await _operation(api, action)
    before = await _counts(api)
    if gate in {"permission", "im-delegate"}:
        await _change(
            api, Role, api.role.id, permissions=["im.delegate"] if gate == "im-delegate" else []
        )
    elif gate == "policy":
        await _change(api, Policy, api.policy.id, effect="DENY")
    elif gate == "default-mask":
        await _change(api, Policy, api.policy.id, enabled=False)
    else:
        await _change(api, Policy, api.policy.id, obligations=[{"type": "not-implemented"}])
    response = await api.client.post(path, json=body)
    _error(response, 403, "project_source_denied")
    assert await _counts(api) == [*before[:4], before[4] + 1, before[5] + 1]
    async with api.sessions() as session:
        audit = await session.scalar(
            select(AuditRecord).where(
                AuditRecord.policy_decision_id
                == UUID(response.json()["details"]["policy_decision_id"])
            )
        )
        assert audit.actor_id == api.user.id and audit.outcome == "DENIED"


@pytest.mark.parametrize(
    "gate", ["workspace", "repository-deny", "configuration", "missing", "foreign"]
)
async def test_resource_rejection_does_not_reveal_target_or_configuration(api, gate):
    version = await _version(api)
    binding = _binding(api, version)
    if gate == "workspace":
        async with api.sessions() as session, session.begin():
            other = User(
                organization_id=api.organization.id,
                external_id="other",
                display_name="其他主体",
                email="other@example.invalid",
            )
            session.add(other)
            await session.flush()
            await session.execute(
                update(Workspace).where(Workspace.id == api.workspace.id).values(owner_id=other.id)
            )
    elif gate == "repository-deny":
        async with api.sessions() as session, session.begin():
            session.add(
                CodeRepositoryGrant(
                    organization_id=api.organization.id,
                    repository_id=api.repository.id,
                    subject_type="USER",
                    subject_value=str(api.user.id),
                    effect="DENY",
                    created_at=utc_now(),
                )
            )
    elif gate == "configuration":
        await _change(
            api, Connector, api.connector.id, configuration={"token": "synthetic-input-marker"}
        )
    elif gate == "missing":
        binding["connector_version_id"] = str(uuid4())
    else:
        async with api.sessions() as session, session.begin():
            organization = Organization(slug=f"foreign-source-{uuid4()}", name="外租户")
            session.add(organization)
            await session.flush()
            foreign_user = User(
                organization_id=organization.id,
                external_id="foreign",
                display_name="外租户主体",
                email="foreign@example.invalid",
            )
            session.add(foreign_user)
            await session.flush()
            foreign = Workspace(
                organization_id=organization.id,
                owner_id=foreign_user.id,
                name="外项目",
                visibility="PRIVATE",
            )
            session.add(foreign)
            await session.flush()
            binding["workspace_id"] = str(foreign.id)
    response = await api.client.post(_PREFIX, json=binding)
    _error(
        response,
        422 if gate == "configuration" else 403,
        "project_source_invalid" if gate == "configuration" else "project_source_denied",
    )
    assert "synthetic-input-marker" not in response.text
    assert await _counts(api) == [1, 0, 0, 0, 2, 2]


@pytest.mark.parametrize(
    "gate", ["anonymous", "invalid-token", "inactive-user", "revoked-session", "origin"]
)
async def test_shared_authentication_stops_before_management_service(api, gate):
    if gate == "anonymous":
        api.client.cookies.clear()
        expected = "authentication_required"
    elif gate == "invalid-token":
        api.client.headers["Authorization"] = "Bearer synthetic-invalid"
        expected = "invalid_token"
    elif gate == "inactive-user":
        await _change(api, User, api.user.id, active=False)
        expected = "unknown_principal"
    elif gate == "revoked-session":
        async with api.sessions() as session, session.begin():
            await AuthSessionStore().revoke(session, api.token)
        expected = "invalid_token"
    else:
        api.client.headers["Origin"] = "https://untrusted.example.invalid"
        expected = "request_origin_denied"
    response = await api.client.post(
        _PREFIX + "/versions", json={"connector_id": str(api.connector.id)}
    )
    _error(response, 403, expected)
    assert await _counts(api) == [0, 0, 0, 0, 0, 0]


@pytest.mark.parametrize("action", ["version", "register", "version-revoke", "source-revoke"])
@pytest.mark.parametrize(
    "field",
    ["organization_id", "actor_id", "permissions", "configuration", "actual_scopes", "environment"],
)
async def test_request_cannot_supply_authority_configuration_or_scopes(api, action, field):
    path, body = await _operation(api, action)
    before = await _counts(api)
    response = await api.client.post(path, json={**body, field: "synthetic-input-marker"})
    _error(response, 422, "request_validation_failed")
    assert "synthetic-input-marker" not in response.text
    assert await _counts(api) == before


@pytest.mark.parametrize(
    "value", ["../escape", "https://host/repo", "a.b", "a//b", "a\nb", "", 42, None, "x" * 501]
)
async def test_provider_id_schema_is_closed(api, value):
    version = await _version(api)
    response = await api.client.post(
        _PREFIX, json={**_binding(api, version), "provider_repository_id": value}
    )
    _error(response, 422, "request_validation_failed")
    assert await _counts(api) == [1, 0, 0, 0, 1, 1]


@pytest.mark.parametrize("action", ["version", "register", "version-revoke", "source-revoke"])
async def test_audit_failure_returns_sanitized_error_without_partial_writes(api, action):
    path, body = await _operation(api, action)
    before = await _counts(api)

    class BrokenAudit:
        async def write(self, *args, **kwargs):
            raise RuntimeError("synthetic-input-marker")

    service = ProjectSourceService(environment="test")
    service.audit = BrokenAudit()
    api.app.dependency_overrides[get_project_source_service] = lambda: service
    response = await api.client.post(path, json=body)
    _error(response, 500, "internal_error")
    assert "synthetic-input-marker" not in response.text
    assert await _counts(api) == before


async def test_outer_commit_failure_does_not_acknowledge_success(api):
    async def failing_session():
        async with api.sessions() as session:

            def reject_commit(current):
                if current.in_nested_transaction():
                    return
                raise RuntimeError("synthetic-commit-failure")

            event.listen(session.sync_session, "before_commit", reject_commit)
            yield session

    api.app.dependency_overrides[get_session] = failing_session
    response = await api.client.post(
        _PREFIX + "/versions", json={"connector_id": str(api.connector.id)}
    )
    _error(response, 500, "internal_error")
    assert "synthetic-commit-failure" not in response.text
    assert await _counts(api) == [0, 0, 0, 0, 0, 0]


async def test_production_environment_denies_without_service_writes(api):
    api.app.state.settings = api.app.state.settings.model_copy(
        update={"environment": Environment.PRODUCTION}
    )
    response = await api.client.post(
        _PREFIX + "/versions", json={"connector_id": str(api.connector.id)}
    )
    _error(response, 403, "project_source_denied")
    assert await _counts(api) == [0, 0, 0, 0, 0, 0]


async def test_openapi_exposes_management_actions_and_inventory_views(api):
    document = api.app.openapi()
    paths = {path for path in document["paths"] if path.startswith(_PREFIX)}
    assert paths == {
        _PREFIX,
        _PREFIX + "/versions",
        _PREFIX + "/versions/{connector_version_id}/revoke",
        _PREFIX + "/{source_id}/revoke",
    }
    assert set(document["paths"][_PREFIX]) == {"get", "post"}
    assert set(document["paths"][_PREFIX + "/versions"]) == {"get", "post"}
    for path in paths:
        for method in document["paths"][path]:
            assert document["paths"][path][method]["security"]
    for path in (_PREFIX, _PREFIX + "/versions"):
        assert {"403", "422", "500"} <= set(document["paths"][path]["get"]["responses"])
    for path in paths - {_PREFIX, _PREFIX + "/versions"}:
        assert {"403", "409", "422", "500"} <= set(document["paths"][path]["post"]["responses"])
    schema = document["components"]["schemas"]["SourceManagementView"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["outcome"]["enum"] == ["CREATED", "UNCHANGED"]
