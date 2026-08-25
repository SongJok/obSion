import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import UUID

import asyncpg
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import ValidationError
from obsion.config import Settings
from obsion.data_intelligence.sql_policy import SqlPolicyValidator
from obsion.db.models import Connector, SecretReference
from obsion.security.identity import Principal


@dataclass(frozen=True, slots=True)
class ConnectorResult:
    data: dict[str, Any]
    source: str
    resource: str
    observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ConnectorContext:
    principal: Principal
    run_id: UUID
    step_id: UUID | None


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
    def __init__(self, settings: Settings) -> None:
        self.timeout = settings.model_request_timeout_seconds

    async def invoke(
        self,
        connector: Connector,
        payload: dict[str, Any],
        credential: str | None,
        context: ConnectorContext,
    ) -> ConnectorResult:
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
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        configured_timeout = int(connector.configuration.get("timeout_seconds", self.timeout))
        async with httpx.AsyncClient(
            timeout=min(configured_timeout, self.timeout), follow_redirects=False
        ) as client:
            response = await client.post(connector.endpoint, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            data = {"items": data}
        return ConnectorResult(
            data=data,
            source=connector.name,
            resource=connector.endpoint,
            observed_at=datetime.now().astimezone(),
        )


def _endpoint_authority(value: str, *, default_scheme: str = "https") -> tuple[str, int]:
    candidate = value if "://" in value else f"{default_scheme}://{value}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("invalid HTTP authority")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.hostname.casefold(), port


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
        dsn = credential or connector.endpoint
        if not dsn:
            raise ValidationError("connector_endpoint_missing", "PostgreSQL connector has no DSN")
        query = payload.get("sql")
        parameters = payload.get("parameters", [])
        parameter_types = payload.get("parameter_types", ["scalar"] * len(parameters))
        if not isinstance(query, str) or not isinstance(parameters, list):
            raise ValidationError("invalid_query_payload", "SQL and parameters are required")
        if not isinstance(parameter_types, list) or len(parameter_types) != len(parameters):
            raise ValidationError("invalid_query_parameters", "SQL parameter metadata is invalid")
        parameters = [
            datetime.fromisoformat(value)
            if parameter_type == "datetime" and isinstance(value, str)
            else value
            for value, parameter_type in zip(parameters, parameter_types, strict=True)
        ]
        allowed_tables = connector.configuration.get("allowed_tables")
        allowed_columns = connector.configuration.get("allowed_columns")
        validation = self.validator.validate(
            query,
            dialect=str(connector.configuration.get("dialect", "postgres")),
            allowed_tables=set(allowed_tables) if isinstance(allowed_tables, list) else set(),
            allowed_columns=set(allowed_columns) if isinstance(allowed_columns, list) else None,
        )
        query = validation.normalized_sql
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
                records = await asyncio.wait_for(
                    connection.fetch(query, *parameters), timeout=self.timeout_seconds
                )
        finally:
            await connection.close(timeout=5)
        rows = [
            {key: _json_value(value) for key, value in record.items()}
            for record in records[: self.max_rows]
        ]
        columns = list(records[0].keys()) if records else []
        return ConnectorResult(
            data={"columns": columns, "rows": rows, "row_count": len(rows)},
            source=connector.name,
            resource=connector.configuration.get("resource_name", connector.name),
            observed_at=datetime.now().astimezone(),
        )


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
