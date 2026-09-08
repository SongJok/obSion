"""User-facing Codeup reads share Policy, Gateway, credentials and Audit."""

from typing import Annotated, Any, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.dependencies import get_capability_gateway
from obsion.api.schemas import APIModel
from obsion.application.project_sources import ProjectSourceService, SourceManagementResult
from obsion.capabilities.codeup_contract import CODEUP_DISCOVERY_OPERATION
from obsion.capabilities.gateway import (
    CapabilityGateway,
    GatewayResult,
    GatewayStatus,
    OperatorGatewayRequest,
)
from obsion.common.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ObsionError,
    ValidationError,
)
from obsion.common.ids import new_id
from obsion.config import Environment, Settings
from obsion.contracts.errors import get_error_code
from obsion.db.models import CodeRepository
from obsion.security.auth import get_app_settings, get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(tags=["codeup"])


class CodeupRequest(APIModel):
    model_config = ConfigDict(extra="forbid")


class CodeupRepositoryRead(CodeupRequest):
    operation: Literal["codeup.repository.get"]


class CodeupCommitRead(CodeupRequest):
    operation: Literal["codeup.commit.get"]
    commit_id: str = Field(pattern="^[a-f0-9]{40}$", min_length=40, max_length=40)


class CodeupCommitsRead(CodeupRequest):
    operation: Literal["codeup.commits.list"]
    ref: str = Field(min_length=1, max_length=200)
    page: int = Field(default=1, ge=1, le=100, strict=True)
    limit: int = Field(default=20, ge=1, le=50, strict=True)


class CodeupFileRead(CodeupRequest):
    operation: Literal["codeup.file.read"]
    commit_id: str = Field(pattern="^[a-f0-9]{40}$", min_length=40, max_length=40)
    path: str = Field(min_length=1, max_length=1024)


class CodeupReadView(APIModel):
    operation: str
    repository: str
    repository_id: UUID
    items: list[dict[str, Any]]
    count: int
    next_page: int | None
    complete: bool
    policy_decision_id: UUID


class CodeupDiscoveryRequest(CodeupRequest):
    operation: Literal["codeup.repositories.discover"]
    search: str | None = Field(default=None, min_length=1, max_length=100)
    page: int = Field(default=1, ge=1, le=100, strict=True)
    limit: int = Field(default=20, ge=1, le=20, strict=True)


class CodeupDiscoveredRepository(APIModel):
    id: str
    name: str
    path: str
    visibility: Literal["private", "internal"]
    archived: bool


class CodeupDiscoveryView(APIModel):
    operation: Literal["codeup.repositories.discover"]
    connector_id: UUID
    items: list[CodeupDiscoveredRepository]
    count: int
    next_page: int | None
    complete: bool
    policy_decision_id: UUID


class CodeupMappingRequest(CodeupRequest):
    """A provider item selected from a prior catalog read."""

    catalog_connector_id: UUID
    repository_id: UUID
    provider_repository_id: str = Field(
        min_length=1,
        max_length=20,
        pattern=r"^[1-9][0-9]{0,19}$",
    )
    provider_path: str = Field(
        min_length=3,
        max_length=500,
        pattern=r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$",
    )


class CodeupMappingView(APIModel):
    connector_id: UUID
    repository_id: UUID
    provider_repository_id: str
    connector_version_id: UUID
    outcome: Literal["CREATED", "UNCHANGED"]
    policy_decision_id: UUID


def _correlation_id(request: Request) -> UUID:
    try:
        return UUID(str(getattr(request.state, "correlation_id", "")))
    except ValueError:
        return new_id()


def _raise_gateway_failure(
    result: GatewayResult, *, message: str, documentation_url: str
) -> NoReturn:
    code = result.error_code or "capability_failed"
    definition = get_error_code(code)
    raise ObsionError(
        code,
        result.error_message or message,
        definition.http_status or (403 if result.status == GatewayStatus.DENIED else 503),
        {
            "policy_decision_id": str(result.policy_decision_id),
            "documentation_url": documentation_url,
        },
    )


def _mapping_result(
    result: SourceManagementResult,
    *,
    connector_id: UUID,
    repository_id: UUID,
    provider_repository_id: str,
) -> CodeupMappingView:
    details = {"policy_decision_id": str(result.policy_decision_id)}
    if result.outcome == "DENIED":
        raise AuthorizationError("project_source_denied", "来源映射操作不被允许", **details)
    if result.outcome == "INVALID":
        raise ValidationError("project_source_invalid", "来源映射参数或连接配置不合法", **details)
    if result.outcome == "CONFLICT":
        raise ConflictError("project_source_conflict", "固定来源绑定冲突", **details)
    if result.record_id is None or result.policy_decision_id is None:
        raise ObsionError("project_source_invalid", "来源映射未生成可用版本", 422, details)
    return CodeupMappingView(
        connector_id=connector_id,
        repository_id=repository_id,
        provider_repository_id=provider_repository_id,
        connector_version_id=result.record_id,
        outcome=result.outcome,
        policy_decision_id=result.policy_decision_id,
    )


@router.post(
    "/admin/codeup/connectors/{connector_id}/repositories",
    response_model=CodeupDiscoveryView,
)
async def discover_codeup_repositories(
    connector_id: UUID,
    body: CodeupDiscoveryRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_app_settings),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> CodeupDiscoveryView:
    try:
        result = await gateway.invoke_operator(
            session,
            OperatorGatewayRequest(
                principal=principal,
                capability_name=CODEUP_DISCOVERY_OPERATION,
                payload=body.model_dump(exclude_none=True),
                resource={"connector_id": str(connector_id), "source": "codeup-catalog"},
                environment=(
                    "development"
                    if settings.environment == Environment.TEST
                    else settings.environment.value
                ),
                correlation_id=_correlation_id(request),
                context={"surface": "codeup-catalog-rest"},
            ),
        )
    except NotFoundError:
        raise ValidationError(
            "codeup_configuration_invalid",
            "该云效目录连接尚未绑定发现能力，请管理员检查连接器和能力绑定。",
        ) from None
    if result.status == GatewayStatus.COMPLETED and result.output is not None:
        return CodeupDiscoveryView.model_validate(
            {**result.output, "policy_decision_id": result.policy_decision_id}
        )
    _raise_gateway_failure(
        result,
        message="云效仓库目录读取失败，请检查连接配置。",
        documentation_url=(
            "https://help.aliyun.com/zh/yunxiao/developer-reference/"
            "listrepositories-query-code-base-list"
        ),
    )


@router.post(
    "/admin/codeup/connectors/{connector_id}/mappings",
    response_model=CodeupMappingView,
)
async def map_codeup_repository(
    connector_id: UUID,
    body: CodeupMappingRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_app_settings),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> CodeupMappingView:
    """Verify one catalog item, then freeze its local Codeup mapping.

    The catalog is re-read through the Gateway so a caller cannot invent a
    provider id or path from an old, untrusted browser response. Mapping itself
    remains a separate Policy/Audit decision and never grants Agent access.
    """

    search = body.provider_path.rsplit("/", 1)[-1][:100]
    discovered: dict[str, Any] | None = None
    page = 1
    for _ in range(5):
        result = await gateway.invoke_operator(
            session,
            OperatorGatewayRequest(
                principal=principal,
                capability_name=CODEUP_DISCOVERY_OPERATION,
                payload={
                    "operation": CODEUP_DISCOVERY_OPERATION,
                    "search": search,
                    "page": page,
                    "limit": 20,
                },
                resource={
                    "connector_id": str(body.catalog_connector_id),
                    "source": "codeup-catalog",
                },
                environment=(
                    "development"
                    if settings.environment == Environment.TEST
                    else settings.environment.value
                ),
                correlation_id=_correlation_id(request),
                context={"surface": "codeup-mapping-rest"},
            ),
        )
        if result.status != GatewayStatus.COMPLETED or result.output is None:
            _raise_gateway_failure(
                result,
                message="云效目录校验失败，未创建来源映射。",
                documentation_url=(
                    "https://help.aliyun.com/zh/yunxiao/developer-reference/"
                    "listrepositories-query-code-base-list"
                ),
            )
        items = result.output.get("items", [])
        if isinstance(items, list):
            match = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict)
                    and item.get("id") == body.provider_repository_id
                    and item.get("path") == body.provider_path
                    and item.get("archived") is False
                ),
                None,
            )
            if match is not None:
                discovered = match
                break
        next_page = result.output.get("next_page")
        if result.output.get("complete") is True or not isinstance(next_page, int):
            break
        page = next_page
    if discovered is None:
        raise AuthorizationError(
            "codeup_repository_denied",
            "该云效仓库未通过当前目录校验，未创建来源映射。",
        )

    service = ProjectSourceService(
        environment=(
            "development"
            if settings.environment == Environment.TEST
            else settings.environment.value
        )
    )
    async with session.begin():
        management = await service.map_codeup_repository(
            session,
            principal,
            connector_id=connector_id,
            repository_id=body.repository_id,
            provider_repository_id=body.provider_repository_id,
        )
    return _mapping_result(
        management,
        connector_id=connector_id,
        repository_id=body.repository_id,
        provider_repository_id=body.provider_repository_id,
    )


@router.post("/code/repositories/{repository_id}/remote-read", response_model=CodeupReadView)
async def read_codeup_repository(
    repository_id: UUID,
    body: Annotated[
        CodeupRepositoryRead | CodeupCommitRead | CodeupCommitsRead | CodeupFileRead,
        Field(discriminator="operation"),
    ],
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_app_settings),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> CodeupReadView:
    # Resolve a same-tenant internal name, never a caller-supplied vendor ID or URL.
    # No content/metadata is returned until Gateway has evaluated Policy + repo ACL.
    async with session.begin():
        name = await session.scalar(
            select(CodeRepository.name).where(
                CodeRepository.id == repository_id,
                CodeRepository.organization_id == principal.organization_id,
                CodeRepository.deleted_at.is_(None),
            )
        )
    if name is None:
        raise NotFoundError("Repository", repository_id)
    try:
        result = await gateway.invoke_operator(
            session,
            OperatorGatewayRequest(
                principal=principal,
                capability_name=body.operation,
                payload={**body.model_dump(), "repository": name},
                resource={
                    "repository": name,
                    "repository_id": str(repository_id),
                    "source": "codeup",
                },
                environment=(
                    "development"
                    if settings.environment == Environment.TEST
                    else settings.environment.value
                ),
                correlation_id=_correlation_id(request),
                context={"surface": "codeup-rest"},
            ),
        )
    except NotFoundError:
        raise ValidationError(
            "codeup_configuration_invalid",
            "该项目尚未绑定云效只读能力，请管理员配置连接器和该仓库的能力绑定。",
        ) from None
    if result.status == GatewayStatus.COMPLETED and result.output is not None:
        return CodeupReadView.model_validate(
            {**result.output, "policy_decision_id": result.policy_decision_id}
        )
    # invoke_operator has committed the decision and failure audit before raising
    # the HTTP error. Never return a vendor body, credentials or arbitrary URLs.
    _raise_gateway_failure(
        result,
        message="云效读取失败，请检查连接配置。",
        documentation_url=(
            "https://help.aliyun.com/zh/yunxiao/developer-reference/"
            "getrepository-query-the-code-base"
        ),
    )
    raise AssertionError("unreachable")
