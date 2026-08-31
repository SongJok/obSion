import asyncio
import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from time import perf_counter
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import UUID

import asyncpg
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.capabilities.circuit_breaker import ConnectorCircuitBreaker
from obsion.capabilities.confluence import (
    CONFLUENCE_KNOWLEDGE_SOURCE,
    CONFLUENCE_OPERATIONS,
    assert_confluence_egress,
    is_confluence_connector,
)
from obsion.capabilities.dingtalk_docs import (
    DINGTALK_DOCS_OPERATIONS,
    DINGTALK_KNOWLEDGE_SOURCE,
    DINGTALK_ORIGIN,
    assert_dingtalk_docs_egress,
    is_dingtalk_docs_connector,
)
from obsion.capabilities.engineering import (
    ENGINEERING_OPERATIONS,
    EngineeringResponseError,
    EngineeringUnavailableError,
)
from obsion.capabilities.engineering import normalize_response as normalize_engineering_response
from obsion.capabilities.feishu_docs import (
    FEISHU_DOCS_OPERATIONS,
    FEISHU_KNOWLEDGE_SOURCE,
    FEISHU_ORIGIN,
    assert_feishu_docs_egress,
    is_feishu_docs_connector,
)
from obsion.capabilities.observability import (
    OBSERVABILITY_OPERATIONS,
    ObservabilityResponseError,
    ObservabilityUnavailableError,
    normalize_response,
)
from obsion.capabilities.vendor_knowledge import (
    KNOWLEDGE_SOURCE_CONTAINERS,
    KNOWLEDGE_SOURCE_ITEMS,
    VendorKnowledgeContainer,
    VendorKnowledgeItem,
    containers_result,
    items_result,
)
from obsion.capabilities.wecom_docs import (
    WECOM_DOCS_OPERATIONS,
    WECOM_KNOWLEDGE_SOURCE,
    WECOM_ORIGIN,
    assert_wecom_docs_egress,
    is_wecom_docs_connector,
)
from obsion.common.errors import ValidationError
from obsion.config import Settings
from obsion.data_intelligence.sql_policy import SqlPolicyValidator
from obsion.db.models import Connector, SecretReference
from obsion.knowledge.connector_contract import KnowledgeConnectorBudget, SyncBudgetTracker
from obsion.security.identity import Principal
from obsion.telemetry import sql_duration


@dataclass(frozen=True, slots=True)
class ConnectorResult:
    data: dict[str, Any]
    source: str
    resource: str
    observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ConnectorContext:
    principal: Principal
    run_id: UUID | None
    step_id: UUID | None
    correlation_id: UUID | None = None
    session: AsyncSession | None = None
    credential: str | None = None


class ConnectorExecutor(Protocol):
    async def invoke(
        self,
        connector: Connector,
        payload: dict[str, Any],
        credential: str | None,
        context: ConnectorContext,
    ) -> ConnectorResult: ...


class CredentialBroker:
    async def resolve(
        self,
        credential_ref: str | None,
        *,
        session: AsyncSession | None = None,
        organization_id: UUID | None = None,
    ) -> str | None:
        if credential_ref is None:
            return None
        scheme, separator, reference = credential_ref.partition("://")
        if not separator or not reference:
            raise ValidationError("invalid_credential_reference", "Credential reference is invalid")
        if scheme == "env":
            value = os.environ.get(reference)
            if value is None:
                raise ValidationError(
                    "credential_unavailable", "The connector credential is not available"
                )
            return value
        if scheme == "secret":
            if session is None or organization_id is None:
                raise ValidationError(
                    "credential_context_missing",
                    "A secret reference requires organization-scoped resolution",
                )
            stored = await session.scalar(
                select(SecretReference).where(
                    SecretReference.organization_id == organization_id,
                    SecretReference.name == reference,
                )
            )
            if stored is None:
                raise ValidationError(
                    "credential_unavailable", "The connector credential is not available"
                )
            return await self.resolve(stored.external_ref)
        raise ValidationError(
            "credential_provider_unsupported",
            "The configured credential provider is not installed",
            provider=scheme,
        )


class HttpJsonExecutor:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        circuit: ConnectorCircuitBreaker | None = None,
        knowledge_service: Any = None,
    ) -> None:
        self.timeout = settings.model_request_timeout_seconds
        self.transport = transport
        self.circuit = circuit or ConnectorCircuitBreaker()
        self.knowledge_service = knowledge_service

    async def invoke(
        self,
        connector: Connector,
        payload: dict[str, Any],
        credential: str | None,
        context: ConnectorContext,
    ) -> ConnectorResult:
        if is_feishu_docs_connector(connector):
            return await self._invoke_feishu_docs(connector, payload, credential, context)
        if is_dingtalk_docs_connector(connector):
            return await self._invoke_dingtalk_docs(connector, payload, credential, context)
        if is_wecom_docs_connector(connector):
            return await self._invoke_wecom_docs(connector, payload, credential, context)
        if is_confluence_connector(connector):
            return await self._invoke_confluence(connector, payload, credential, context)
        if _is_observability_connector(connector):
            return await self._invoke_observability(connector, payload, credential, context)
        if _is_engineering_connector(connector):
            return await self._invoke_engineering(connector, payload, credential, context)
        del context
        if not connector.endpoint:
            raise ValidationError("connector_endpoint_missing", "HTTP connector has no endpoint")
        endpoint = urlparse(connector.endpoint)
        try:
            endpoint_authority = _endpoint_authority(connector.endpoint)
            allowed_authorities = {
                _endpoint_authority(item, default_scheme=endpoint.scheme)
                for item in connector.allowed_egress
                if isinstance(item, str)
            }
        except ValueError as exc:
            raise ValidationError(
                "connector_egress_invalid", "Connector egress configuration is invalid"
            ) from exc
        if (
            endpoint.scheme not in {"https", "http"}
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint_authority not in allowed_authorities
        ):
            raise ValidationError(
                "connector_egress_denied", "Connector endpoint is outside its egress allowlist"
            )
        if endpoint.scheme == "http" and connector.environment != "development":
            raise ValidationError(
                "connector_tls_required", "Non-development HTTP connectors must use TLS"
            )
        self.circuit.guard(endpoint_authority)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        configured_timeout = int(connector.configuration.get("timeout_seconds", self.timeout))
        try:
            async with httpx.AsyncClient(
                timeout=min(configured_timeout, self.timeout),
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.post(connector.endpoint, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, TimeoutError, OSError):
            self.circuit.record_failure(endpoint_authority)
            raise
        self.circuit.record_success(endpoint_authority)
        if not isinstance(data, dict):
            data = {"items": data}
        return ConnectorResult(
            data=data,
            source=connector.name,
            resource=connector.endpoint,
            observed_at=datetime.now().astimezone(),
        )

    async def _invoke_observability(
        self,
        connector: Connector,
        payload: dict[str, Any],
        credential: str | None,
        context: ConnectorContext,
    ) -> ConnectorResult:
        del context
        operation = payload.get("operation")
        if not isinstance(operation, str) or operation not in OBSERVABILITY_OPERATIONS:
            raise ValidationError(
                "observability_operation_invalid",
                "The observability operation is not part of the read-only contract",
            )
        if not connector.endpoint:
            raise ValidationError("connector_endpoint_missing", "HTTP connector has no endpoint")
        endpoint_url = connector.endpoint
        paths = connector.configuration.get("operation_paths")
        if isinstance(paths, dict) and isinstance(paths.get(operation), str):
            path = paths[operation].strip()
            if path.startswith("/"):
                endpoint_url = connector.endpoint.rstrip("/") + path
            elif path:
                endpoint_url = connector.endpoint.rstrip("/") + "/" + path
        endpoint = urlparse(endpoint_url)
        try:
            endpoint_authority = _endpoint_authority(endpoint_url)
            allowed_authorities = {
                _endpoint_authority(item, default_scheme=endpoint.scheme)
                for item in connector.allowed_egress
                if isinstance(item, str)
            }
        except ValueError as exc:
            raise ValidationError(
                "connector_egress_invalid", "Connector egress configuration is invalid"
            ) from exc
        if (
            endpoint.scheme not in {"https", "http"}
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint_authority not in allowed_authorities
        ):
            raise ValidationError(
                "connector_egress_denied", "Connector endpoint is outside its egress allowlist"
            )
        if endpoint.scheme == "http" and connector.environment != "development":
            raise ValidationError(
                "connector_tls_required", "Non-development HTTP connectors must use TLS"
            )
        self.circuit.guard(endpoint_authority)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        configured_timeout = int(connector.configuration.get("timeout_seconds", self.timeout))
        try:
            async with httpx.AsyncClient(
                timeout=min(configured_timeout, self.timeout),
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.post(endpoint_url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            self.circuit.record_failure(endpoint_authority)
            raise ObservabilityUnavailableError("The observability connector timed out") from exc
        except httpx.HTTPError as exc:
            self.circuit.record_failure(endpoint_authority)
            raise ObservabilityUnavailableError() from exc
        if response.is_error:
            self.circuit.record_failure(endpoint_authority)
            raise ObservabilityUnavailableError(
                "The observability connector returned an upstream error"
            )
        self.circuit.record_success(endpoint_authority)
        try:
            response_payload = response.json()
        except ValueError as exc:
            raise ObservabilityResponseError() from exc
        if isinstance(response_payload, Mapping) and response_payload.get("error"):
            raise ObservabilityResponseError()
        try:
            normalized = normalize_response(
                response_payload,
                operation=operation,
                default_service=_payload_string(
                    payload.get("service"),
                    connector.configuration.get("default_service"),
                    "*",
                ),
                default_environment=_payload_string(
                    payload.get("environment"), connector.environment
                ),
            )
        except (TypeError, ValueError) as exc:
            raise ObservabilityResponseError() from exc
        return ConnectorResult(
            data=normalized,
            source=connector.name,
            resource=f"{endpoint_url}#{operation}",
            observed_at=datetime.now().astimezone(),
        )

    async def _invoke_engineering(
        self,
        connector: Connector,
        payload: dict[str, Any],
        credential: str | None,
        context: ConnectorContext,
    ) -> ConnectorResult:
        del context
        operation = payload.get("operation")
        if not isinstance(operation, str) or operation not in ENGINEERING_OPERATIONS:
            raise ValidationError(
                "engineering_operation_invalid",
                "The engineering operation is not part of the read-only contract",
            )
        if not connector.endpoint:
            raise ValidationError("connector_endpoint_missing", "HTTP connector has no endpoint")
        endpoint_url = connector.endpoint
        paths = connector.configuration.get("operation_paths")
        if isinstance(paths, dict) and isinstance(paths.get(operation), str):
            path = paths[operation].strip()
            if path.startswith("/"):
                endpoint_url = connector.endpoint.rstrip("/") + path
            elif path:
                endpoint_url = connector.endpoint.rstrip("/") + "/" + path
        endpoint = urlparse(endpoint_url)
        try:
            endpoint_authority = _endpoint_authority(endpoint_url)
            allowed_authorities = {
                _endpoint_authority(item, default_scheme=endpoint.scheme)
                for item in connector.allowed_egress
                if isinstance(item, str)
            }
        except ValueError as exc:
            raise ValidationError(
                "connector_egress_invalid", "Connector egress configuration is invalid"
            ) from exc
        if (
            endpoint.scheme not in {"https", "http"}
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint_authority not in allowed_authorities
        ):
            raise ValidationError(
                "connector_egress_denied", "Connector endpoint is outside its egress allowlist"
            )
        if endpoint.scheme == "http" and connector.environment != "development":
            raise ValidationError(
                "connector_tls_required", "Non-development HTTP connectors must use TLS"
            )
        allowed_repositories = connector.configuration.get("allowed_repositories")
        repository = payload.get("repository")
        if (
            isinstance(allowed_repositories, list)
            and allowed_repositories
            and (not isinstance(repository, str) or repository not in allowed_repositories)
        ):
            raise ValidationError(
                "engineering_repository_denied",
                "The repository is not allowed by the engineering connector",
            )
        self.circuit.guard(endpoint_authority)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        configured_timeout = int(connector.configuration.get("timeout_seconds", self.timeout))
        try:
            async with httpx.AsyncClient(
                timeout=min(configured_timeout, self.timeout),
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.post(endpoint_url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            self.circuit.record_failure(endpoint_authority)
            raise EngineeringUnavailableError("The engineering connector timed out") from exc
        except httpx.HTTPError as exc:
            self.circuit.record_failure(endpoint_authority)
            raise EngineeringUnavailableError() from exc
        if response.is_error:
            self.circuit.record_failure(endpoint_authority)
            raise EngineeringUnavailableError(
                "The engineering connector returned an upstream error"
            )
        self.circuit.record_success(endpoint_authority)
        try:
            response_payload = response.json()
        except ValueError as exc:
            raise EngineeringResponseError() from exc
        if isinstance(response_payload, Mapping) and response_payload.get("error"):
            raise EngineeringResponseError()
        try:
            normalized = normalize_engineering_response(
                response_payload,
                operation=operation,
                default_repository=_payload_string(
                    payload.get("repository"),
                    connector.configuration.get("default_repository"),
                    "*",
                ),
                default_environment=_payload_string(
                    payload.get("environment"), connector.environment
                ),
            )
        except (TypeError, ValueError) as exc:
            raise EngineeringResponseError() from exc
        return ConnectorResult(
            data=normalized,
            source=connector.name,
            resource=f"{endpoint_url}#{operation}",
            observed_at=datetime.now().astimezone(),
        )

    async def _invoke_feishu_docs(
        self,
        connector: Connector,
        payload: dict[str, Any],
        credential: str | None,
        context: ConnectorContext,
    ) -> ConnectorResult:
        from obsion.domain.enums import Classification
        from obsion.knowledge.feishu import (
            ingest_feishu_document,
            ingest_result_payload,
            list_feishu_nodes,
            list_feishu_spaces,
            sync_feishu_space,
        )

        operation = payload.get("operation")
        if not isinstance(operation, str) or operation not in FEISHU_DOCS_OPERATIONS:
            raise ValidationError(
                "feishu_docs_operation_invalid",
                "The Feishu docs operation is not part of the Knowledge source contract",
            )
        if context.session is None:
            raise ValidationError(
                "feishu_docs_operation_invalid",
                "Feishu Knowledge source access requires a durable control-plane session",
            )
        assert_feishu_docs_egress(connector)
        if operation == KNOWLEDGE_SOURCE_CONTAINERS:
            tracker = SyncBudgetTracker(KnowledgeConnectorBudget.from_connector(connector))
            spaces = await list_feishu_spaces(
                context.session,
                context.principal,
                connector=connector,
                credential=credential,
                transport=self.transport,
                tracker=tracker,
            )
            return ConnectorResult(
                data=containers_result(
                    FEISHU_KNOWLEDGE_SOURCE,
                    (
                        VendorKnowledgeContainer(
                            container_id=space.space_id,
                            name=space.name,
                            description=space.description,
                        )
                        for space in spaces
                    ),
                ),
                source=connector.name,
                resource=f"{FEISHU_ORIGIN}#wiki",
                observed_at=datetime.now().astimezone(),
            )
        if operation == KNOWLEDGE_SOURCE_ITEMS:
            tracker = SyncBudgetTracker(KnowledgeConnectorBudget.from_connector(connector))
            container_id = str(payload.get("container_id") or "")
            nodes = await list_feishu_nodes(
                context.session,
                context.principal,
                container_id,
                connector=connector,
                credential=credential,
                transport=self.transport,
                tracker=tracker,
            )
            return ConnectorResult(
                data=items_result(
                    FEISHU_KNOWLEDGE_SOURCE,
                    (
                        VendorKnowledgeItem(
                            container_id=node.space_id,
                            item_id=node.node_token,
                            document_id=node.obj_token,
                            item_type=node.obj_type,
                            title=node.title,
                        )
                        for node in nodes
                    ),
                ),
                source=connector.name,
                resource=f"{FEISHU_ORIGIN}#wiki/{container_id}",
                observed_at=datetime.now().astimezone(),
            )
        if self.knowledge_service is None:
            raise ValidationError(
                "feishu_docs_operation_invalid",
                "Feishu document ingest is not wired to KnowledgeService",
            )
        classification_value = payload.get("classification", Classification.INTERNAL.value)
        try:
            classification = Classification(classification_value)
        except ValueError as exc:
            raise ValidationError(
                "feishu_docs_operation_invalid",
                "The Feishu document classification is invalid",
            ) from exc
        acl = payload.get("acl")
        if acl is not None and not isinstance(acl, dict):
            raise ValidationError("document_acl_invalid", "Document ACL must be a JSON object")
        if operation == "knowledge.sync":
            result = await sync_feishu_space(
                context.session,
                context.principal,
                self.knowledge_service,
                space_id=str(payload.get("space_id") or ""),
                classification=classification,
                acl=acl,
                inherit_acl=payload.get("inherit_acl") is True,
                connector=connector,
                credential=credential,
                transport=self.transport,
            )
            return ConnectorResult(
                data=result,
                source=connector.name,
                resource=f"{FEISHU_ORIGIN}#wiki/{result['space_id']}",
                observed_at=datetime.now().astimezone(),
            )
        document, version, count, fetched = await ingest_feishu_document(
            context.session,
            context.principal,
            self.knowledge_service,
            document_id=str(payload.get("document_id") or ""),
            obj_type=str(payload.get("obj_type") or "auto"),
            title=payload.get("title") if isinstance(payload.get("title"), str) else None,
            classification=classification,
            acl=acl,
            inherit_acl=payload.get("inherit_acl") is True,
            connector=connector,
            credential=credential,
            transport=self.transport,
        )
        return ConnectorResult(
            data=ingest_result_payload(document, version, count, fetched),
            source=connector.name,
            resource=f"{FEISHU_ORIGIN}#{FEISHU_KNOWLEDGE_SOURCE}/{fetched.document_id}",
            observed_at=datetime.now().astimezone(),
        )

    async def _invoke_dingtalk_docs(
        self,
        connector: Connector,
        payload: dict[str, Any],
        credential: str | None,
        context: ConnectorContext,
    ) -> ConnectorResult:
        from obsion.domain.enums import Classification
        from obsion.knowledge.dingtalk import (
            ingest_dingtalk_document,
            ingest_result_payload,
            list_dingtalk_workspace_nodes,
            list_dingtalk_workspaces,
            sync_dingtalk_workspace,
        )

        operation = payload.get("operation")
        if not isinstance(operation, str) or operation not in DINGTALK_DOCS_OPERATIONS:
            raise ValidationError(
                "dingtalk_docs_operation_invalid",
                "The DingTalk docs operation is not part of the Knowledge source contract",
            )
        if context.session is None:
            raise ValidationError(
                "dingtalk_docs_operation_invalid",
                "DingTalk Knowledge source access requires a durable control-plane session",
            )
        assert_dingtalk_docs_egress(connector)
        if operation == KNOWLEDGE_SOURCE_CONTAINERS:
            tracker = SyncBudgetTracker(KnowledgeConnectorBudget.from_connector(connector))
            workspaces = await list_dingtalk_workspaces(
                context.session,
                context.principal,
                connector=connector,
                credential=credential,
                transport=self.transport,
                tracker=tracker,
            )
            return ConnectorResult(
                data=containers_result(
                    DINGTALK_KNOWLEDGE_SOURCE,
                    (
                        VendorKnowledgeContainer(
                            container_id=workspace.workspace_id,
                            name=workspace.name,
                            description=workspace.description,
                        )
                        for workspace in workspaces
                    ),
                ),
                source=connector.name,
                resource=f"{DINGTALK_ORIGIN}#workspaces",
                observed_at=datetime.now().astimezone(),
            )
        if operation == KNOWLEDGE_SOURCE_ITEMS:
            tracker = SyncBudgetTracker(KnowledgeConnectorBudget.from_connector(connector))
            container_id = str(payload.get("container_id") or "")
            nodes = await list_dingtalk_workspace_nodes(
                context.session,
                context.principal,
                container_id,
                connector=connector,
                credential=credential,
                transport=self.transport,
                tracker=tracker,
            )
            return ConnectorResult(
                data=items_result(
                    DINGTALK_KNOWLEDGE_SOURCE,
                    (
                        VendorKnowledgeItem(
                            container_id=node.workspace_id,
                            item_id=node.node_id,
                            document_id=node.document_id,
                            item_type=node.node_type,
                            title=node.title,
                        )
                        for node in nodes
                    ),
                ),
                source=connector.name,
                resource=f"{DINGTALK_ORIGIN}#workspaces/{container_id}",
                observed_at=datetime.now().astimezone(),
            )
        if self.knowledge_service is None:
            raise ValidationError(
                "dingtalk_docs_operation_invalid",
                "DingTalk document ingest is not wired to KnowledgeService",
            )
        classification_value = payload.get("classification", Classification.INTERNAL.value)
        try:
            classification = Classification(classification_value)
        except ValueError as exc:
            raise ValidationError(
                "dingtalk_docs_operation_invalid",
                "The DingTalk document classification is invalid",
            ) from exc
        acl = payload.get("acl")
        if acl is not None and not isinstance(acl, dict):
            raise ValidationError("document_acl_invalid", "Document ACL must be a JSON object")
        if operation == "knowledge.sync":
            result = await sync_dingtalk_workspace(
                context.session,
                context.principal,
                self.knowledge_service,
                workspace_id=str(payload.get("workspace_id") or payload.get("space_id") or ""),
                classification=classification,
                acl=acl,
                inherit_acl=payload.get("inherit_acl") is True,
                connector=connector,
                credential=credential,
                transport=self.transport,
            )
            return ConnectorResult(
                data=result,
                source=connector.name,
                resource=f"{DINGTALK_ORIGIN}#workspace/{result['workspace_id']}",
                observed_at=datetime.now().astimezone(),
            )
        document, version, count, fetched = await ingest_dingtalk_document(
            context.session,
            context.principal,
            self.knowledge_service,
            document_id=str(payload.get("document_id") or ""),
            title=payload.get("title") if isinstance(payload.get("title"), str) else None,
            classification=classification,
            acl=acl,
            inherit_acl=payload.get("inherit_acl") is True,
            connector=connector,
            credential=credential,
            transport=self.transport,
        )
        return ConnectorResult(
            data=ingest_result_payload(document, version, count, fetched),
            source=connector.name,
            resource=f"{DINGTALK_ORIGIN}#{DINGTALK_KNOWLEDGE_SOURCE}/{fetched.document_id}",
            observed_at=datetime.now().astimezone(),
        )

    async def _invoke_wecom_docs(
        self,
        connector: Connector,
        payload: dict[str, Any],
        credential: str | None,
        context: ConnectorContext,
    ) -> ConnectorResult:
        from obsion.domain.enums import Classification
        from obsion.knowledge.wecom import (
            describe_wecom_space,
            ingest_result_payload,
            ingest_wecom_document,
            list_wecom_space_nodes,
            sync_wecom_space,
        )

        operation = payload.get("operation")
        if not isinstance(operation, str) or operation not in WECOM_DOCS_OPERATIONS:
            raise ValidationError(
                "wecom_docs_operation_invalid",
                "The WeCom docs operation is not part of the Knowledge source contract",
            )
        if context.session is None:
            raise ValidationError(
                "wecom_docs_operation_invalid",
                "WeCom Knowledge source access requires a durable control-plane session",
            )
        assert_wecom_docs_egress(connector)
        if operation == KNOWLEDGE_SOURCE_CONTAINERS:
            container_id = str(payload.get("container_id") or "")
            space = await describe_wecom_space(
                context.session,
                context.principal,
                container_id,
                connector=connector,
                credential=credential,
                transport=self.transport,
            )
            return ConnectorResult(
                data=containers_result(
                    WECOM_KNOWLEDGE_SOURCE,
                    (
                        VendorKnowledgeContainer(
                            container_id=space.space_id,
                            name=space.name,
                            description=space.description,
                        ),
                    ),
                ),
                source=connector.name,
                resource=f"{WECOM_ORIGIN}#spaces/{container_id}",
                observed_at=datetime.now().astimezone(),
            )
        if operation == KNOWLEDGE_SOURCE_ITEMS:
            tracker = SyncBudgetTracker(KnowledgeConnectorBudget.from_connector(connector))
            container_id = str(payload.get("container_id") or "")
            nodes = await list_wecom_space_nodes(
                context.session,
                context.principal,
                container_id,
                connector=connector,
                credential=credential,
                transport=self.transport,
                tracker=tracker,
            )
            return ConnectorResult(
                data=items_result(
                    WECOM_KNOWLEDGE_SOURCE,
                    (
                        VendorKnowledgeItem(
                            container_id=node.space_id,
                            item_id=node.node_id,
                            document_id=node.document_id,
                            item_type=node.node_type,
                            title=node.title,
                        )
                        for node in nodes
                    ),
                ),
                source=connector.name,
                resource=f"{WECOM_ORIGIN}#spaces/{container_id}",
                observed_at=datetime.now().astimezone(),
            )
        if self.knowledge_service is None:
            raise ValidationError(
                "wecom_docs_operation_invalid",
                "WeCom document ingest is not wired to KnowledgeService",
            )
        classification_value = payload.get("classification", Classification.INTERNAL.value)
        try:
            classification = Classification(classification_value)
        except ValueError as exc:
            raise ValidationError(
                "wecom_docs_operation_invalid",
                "The WeCom document classification is invalid",
            ) from exc
        acl = payload.get("acl")
        if acl is not None and not isinstance(acl, dict):
            raise ValidationError("document_acl_invalid", "Document ACL must be a JSON object")
        if operation == "knowledge.sync":
            result = await sync_wecom_space(
                context.session,
                context.principal,
                self.knowledge_service,
                space_id=str(payload.get("space_id") or payload.get("workspace_id") or ""),
                classification=classification,
                acl=acl,
                inherit_acl=payload.get("inherit_acl") is True,
                connector=connector,
                credential=credential,
                transport=self.transport,
            )
            return ConnectorResult(
                data=result,
                source=connector.name,
                resource=f"{WECOM_ORIGIN}#space/{result['space_id']}",
                observed_at=datetime.now().astimezone(),
            )
        document, version, count, fetched = await ingest_wecom_document(
            context.session,
            context.principal,
            self.knowledge_service,
            document_id=str(payload.get("document_id") or ""),
            title=payload.get("title") if isinstance(payload.get("title"), str) else None,
            classification=classification,
            acl=acl,
            inherit_acl=payload.get("inherit_acl") is True,
            connector=connector,
            credential=credential,
            transport=self.transport,
        )
        return ConnectorResult(
            data=ingest_result_payload(document, version, count, fetched),
            source=connector.name,
            resource=f"{WECOM_ORIGIN}#{WECOM_KNOWLEDGE_SOURCE}/{fetched.document_id}",
            observed_at=datetime.now().astimezone(),
        )

    async def _invoke_confluence(
        self,
        connector: Connector,
        payload: dict[str, Any],
        credential: str | None,
        context: ConnectorContext,
    ) -> ConnectorResult:
        from obsion.domain.enums import Classification
        from obsion.knowledge.confluence import (
            ingest_confluence_page,
            ingest_result_payload,
            list_confluence_pages,
            list_confluence_spaces,
            sync_confluence_space,
        )

        operation = payload.get("operation")
        if not isinstance(operation, str) or operation not in CONFLUENCE_OPERATIONS:
            raise ValidationError(
                "confluence_operation_invalid",
                "The Confluence operation is not part of the Knowledge source contract",
            )
        if context.session is None:
            raise ValidationError(
                "confluence_operation_invalid",
                "Confluence Knowledge source access requires a durable control-plane session",
            )
        assert_confluence_egress(connector)
        if operation == KNOWLEDGE_SOURCE_CONTAINERS:
            tracker = SyncBudgetTracker(KnowledgeConnectorBudget.from_connector(connector))
            spaces = await list_confluence_spaces(
                context.session,
                context.principal,
                connector=connector,
                credential=credential,
                transport=self.transport,
                tracker=tracker,
            )
            return ConnectorResult(
                data=containers_result(
                    CONFLUENCE_KNOWLEDGE_SOURCE,
                    (
                        VendorKnowledgeContainer(
                            container_id=space.space_id,
                            name=space.name,
                            key=space.key,
                        )
                        for space in spaces
                    ),
                ),
                source=connector.name,
                resource=f"{connector.endpoint}#spaces",
                observed_at=datetime.now().astimezone(),
            )
        if operation == KNOWLEDGE_SOURCE_ITEMS:
            tracker = SyncBudgetTracker(KnowledgeConnectorBudget.from_connector(connector))
            container_id = str(payload.get("container_id") or "")
            pages = await list_confluence_pages(
                context.session,
                context.principal,
                container_id,
                connector=connector,
                credential=credential,
                transport=self.transport,
                tracker=tracker,
            )
            return ConnectorResult(
                data=items_result(
                    CONFLUENCE_KNOWLEDGE_SOURCE,
                    (
                        VendorKnowledgeItem(
                            container_id=page.space_id,
                            item_id=page.page_id,
                            document_id=page.page_id,
                            item_type="page",
                            title=page.title,
                            status=page.status,
                        )
                        for page in pages
                    ),
                ),
                source=connector.name,
                resource=f"{connector.endpoint}#spaces/{container_id}",
                observed_at=datetime.now().astimezone(),
            )
        if self.knowledge_service is None:
            raise ValidationError(
                "confluence_operation_invalid",
                "Confluence page ingest is not wired to KnowledgeService",
            )
        classification_value = payload.get("classification", Classification.INTERNAL.value)
        try:
            classification = Classification(classification_value)
        except ValueError as exc:
            raise ValidationError(
                "confluence_operation_invalid",
                "The Confluence page classification is invalid",
            ) from exc
        acl = payload.get("acl")
        if acl is not None and not isinstance(acl, dict):
            raise ValidationError("document_acl_invalid", "Document ACL must be a JSON object")
        if operation == "knowledge.sync":
            result = await sync_confluence_space(
                context.session,
                context.principal,
                self.knowledge_service,
                space_id=str(payload.get("space_id") or ""),
                classification=classification,
                acl=acl,
                inherit_acl=payload.get("inherit_acl") is True,
                connector=connector,
                credential=credential,
                transport=self.transport,
            )
            return ConnectorResult(
                data=result,
                source=connector.name,
                resource=f"{connector.endpoint}#wiki/{result['space_id']}",
                observed_at=datetime.now().astimezone(),
            )
        page, version, count, fetched = await ingest_confluence_page(
            context.session,
            context.principal,
            self.knowledge_service,
            page_id=str(payload.get("page_id") or payload.get("document_id") or ""),
            title=payload.get("title") if isinstance(payload.get("title"), str) else None,
            classification=classification,
            acl=acl,
            inherit_acl=payload.get("inherit_acl") is True,
            connector=connector,
            credential=credential,
            transport=self.transport,
        )
        return ConnectorResult(
            data=ingest_result_payload(page, version, count, fetched),
            source=connector.name,
            resource=f"{connector.endpoint}#{CONFLUENCE_KNOWLEDGE_SOURCE}/{fetched.page_id}",
            observed_at=datetime.now().astimezone(),
        )


def _endpoint_authority(value: str, *, default_scheme: str = "https") -> tuple[str, int]:
    candidate = value if "://" in value else f"{default_scheme}://{value}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("invalid HTTP authority")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.hostname.casefold(), port


def _is_observability_connector(connector: Connector) -> bool:
    connector_type = connector.connector_type.casefold()
    protocol = connector.configuration.get("protocol")
    return connector_type in {"observability", "observability-http", "http-observability"} or (
        isinstance(protocol, str) and protocol.casefold() == "observability.v1"
    )


def _is_engineering_connector(connector: Connector) -> bool:
    connector_type = connector.connector_type.casefold()
    protocol = connector.configuration.get("protocol")
    return connector_type in {"engineering", "engineering-http", "http-engineering"} or (
        isinstance(protocol, str) and protocol.casefold() == "engineering.v1"
    )


def _payload_string(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "*"


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return f"<binary:{len(value)}>"
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


class PostgresReadOnlyExecutor:
    def __init__(self, settings: Settings) -> None:
        self.max_rows = settings.sql_max_limit
        self.scan_budget = settings.sql_scan_budget
        self.timeout_seconds = settings.sql_timeout_seconds
        self.validator = SqlPolicyValidator(
            default_limit=settings.sql_default_limit,
            max_limit=settings.sql_max_limit,
        )

    async def invoke(
        self,
        connector: Connector,
        payload: dict[str, Any],
        credential: str | None,
        context: ConnectorContext,
    ) -> ConnectorResult:
        del context
        started = perf_counter()
        status = "FAILED"
        try:
            dsn = credential or connector.endpoint
            if not dsn:
                raise ValidationError(
                    "connector_endpoint_missing", "PostgreSQL connector has no DSN"
                )
            if (
                connector.configuration.get("role") != "read_replica"
                or connector.configuration.get("read_only") is False
            ):
                raise ValidationError(
                    "sql_primary_source_denied",
                    "SQL execution is only available through a read-only replica",
                )
            query = payload.get("sql")
            parameters = payload.get("parameters", [])
            parameter_types = payload.get("parameter_types", ["scalar"] * len(parameters))
            if not isinstance(query, str) or not isinstance(parameters, list):
                raise ValidationError("invalid_query_payload", "SQL and parameters are required")
            if not isinstance(parameter_types, list) or len(parameter_types) != len(parameters):
                raise ValidationError(
                    "invalid_query_parameters", "SQL parameter metadata is invalid"
                )
            parameters = [
                datetime.fromisoformat(value)
                if parameter_type == "datetime" and isinstance(value, str)
                else value
                for value, parameter_type in zip(parameters, parameter_types, strict=True)
            ]
            allowed_tables = connector.configuration.get("allowed_tables")
            allowed_columns = connector.configuration.get("allowed_columns")
            configured_budget = connector.configuration.get("scan_budget", self.scan_budget)
            scan_budget = (
                configured_budget
                if isinstance(configured_budget, int) and not isinstance(configured_budget, bool)
                else self.scan_budget
            )
            require_limit = connector.configuration.get("require_explicit_limit", True)
            validation = self.validator.validate(
                query,
                dialect=str(connector.configuration.get("dialect", "postgres")),
                allowed_tables=set(allowed_tables) if isinstance(allowed_tables, list) else set(),
                allowed_columns=(
                    set(allowed_columns) if isinstance(allowed_columns, list) else None
                ),
                require_limit=bool(require_limit),
                scan_budget=scan_budget,
            )
            query = validation.normalized_sql
            explain_only = payload.get("explain") is True
            connection = await asyncpg.connect(dsn=dsn, statement_cache_size=0, timeout=10)
            try:
                async with connection.transaction(readonly=True):
                    timeout_ms = (
                        min(
                            int(payload.get("timeout_seconds", self.timeout_seconds)),
                            self.timeout_seconds,
                        )
                        * 1000
                    )
                    await connection.execute(f"SET LOCAL statement_timeout = {timeout_ms:d}")
                    estimated_scan_cost = validation.estimated_scan_cost
                    if validation.statement_type != "EXPLAIN":
                        estimated_scan_cost = await self._explain_scan_cost(
                            connection,
                            validation.normalized_sql,
                            parameters,
                            dialect=str(connector.configuration.get("dialect", "postgres")),
                        )
                    if estimated_scan_cost > scan_budget:
                        raise ValidationError(
                            "sql_scan_budget_exceeded",
                            "The query exceeds the configured scan budget",
                            estimated_scan_cost=estimated_scan_cost,
                            scan_budget=scan_budget,
                        )
                    if explain_only and validation.statement_type != "EXPLAIN":
                        query = "EXPLAIN (FORMAT JSON) " + query
                    records = await asyncio.wait_for(
                        connection.fetch(query, *parameters), timeout=self.timeout_seconds
                    )
            finally:
                await connection.close(timeout=5)
            columns = list(records[0].keys()) if records else []
            masks = payload.get("column_masks", {})
            rows = [
                {
                    key: _json_value(_mask_column_value(value, masks.get(key)))
                    for key, value in record.items()
                }
                for record in records[: self.max_rows]
            ]
            status = "SUCCESS"
            if explain_only:
                return ConnectorResult(
                    data={
                        "plan": {"columns": columns, "rows": rows, "row_count": len(rows)},
                        "validation": _validation_payload(validation, estimated_scan_cost),
                    },
                    source=connector.name,
                    resource=connector.configuration.get("resource_name", connector.name),
                    observed_at=datetime.now().astimezone(),
                )
            return ConnectorResult(
                data={"columns": columns, "rows": rows, "row_count": len(rows)},
                source=connector.name,
                resource=connector.configuration.get("resource_name", connector.name),
                observed_at=datetime.now().astimezone(),
            )
        finally:
            sql_duration.record((perf_counter() - started) * 1000, {"status": status})

    async def _explain_scan_cost(
        self,
        connection: asyncpg.Connection,
        query: str,
        parameters: list[Any],
        *,
        dialect: str,
    ) -> int:
        if dialect.casefold() not in {"postgres", "postgresql"}:
            return 0
        try:
            rows = await connection.fetch("EXPLAIN (FORMAT JSON) " + query, *parameters)
        except Exception as exc:
            raise ValidationError(
                "sql_scan_budget_unavailable", "The database could not produce a scan estimate"
            ) from exc
        if not rows:
            return 0
        document = rows[0][0]
        return _plan_scan_cost(document)


def _plan_scan_cost(value: Any) -> int:
    """Extract a conservative integer scan estimate from PostgreSQL EXPLAIN JSON."""
    if isinstance(value, list):
        return max((_plan_scan_cost(item) for item in value), default=0)
    if isinstance(value, dict):
        own_rows = value.get("Plan Rows")
        own_cost = value.get("Total Cost")
        candidates = [_plan_scan_cost(item) for item in value.values()]
        estimate = 0
        if isinstance(own_rows, (int, float)) and own_rows >= 0:
            estimate = max(estimate, int(own_rows))
        if isinstance(own_cost, (int, float)) and own_cost >= 0:
            estimate = max(estimate, int(own_cost))
        return max([estimate, *candidates], default=0)
    return 0


def _validation_payload(validation: Any, estimated_scan_cost: int) -> dict[str, Any]:
    payload = asdict(validation)
    payload["tables"] = list(validation.tables)
    payload["columns"] = list(validation.columns)
    payload["warnings"] = list(validation.warnings)
    payload["estimated_scan_cost"] = estimated_scan_cost
    return payload


def _mask_column_value(value: Any, policy: Any) -> Any:
    if not isinstance(policy, dict) or policy.get("enabled") is False:
        return value
    mode = str(policy.get("mode", "mask")).casefold()
    if mode == "hash":
        return hashlib.sha256(str(value).encode()).hexdigest()[:16]
    if mode in {"mask", "redact", "hidden"}:
        return str(policy.get("replacement", "***"))
    return value


InternalHandler = Callable[
    [dict[str, Any], Connector, ConnectorContext], Awaitable[ConnectorResult]
]


class InternalExecutor:
    def __init__(self) -> None:
        self._handlers: dict[str, InternalHandler] = {}

    def register(self, connector_type: str, handler: InternalHandler) -> None:
        if connector_type in self._handlers:
            raise ValueError(f"Internal connector handler already registered: {connector_type}")
        self._handlers[connector_type] = handler

    async def invoke(
        self,
        connector: Connector,
        payload: dict[str, Any],
        credential: str | None,
        context: ConnectorContext,
    ) -> ConnectorResult:
        del credential
        handler = self._handlers.get(connector.connector_type)
        if handler is None:
            raise ValidationError(
                "connector_handler_missing",
                "No internal handler is registered for this connector type",
                connector_type=connector.connector_type,
            )
        return await handler(payload, connector, context)
