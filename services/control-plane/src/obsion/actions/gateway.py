import asyncio
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.capabilities.connectors import CredentialBroker
from obsion.capabilities.rate_limit import (
    CapabilityRateLimiter,
    InMemoryFixedWindowRateLimiter,
    RateLimitUnavailable,
)
from obsion.common.errors import NotFoundError, ValidationError
from obsion.common.time import ensure_utc, utc_now
from obsion.contracts.errors import validate_error_code
from obsion.db.models import (
    ActionApproval,
    ActionAttempt,
    ActionRequest,
    CapabilityBinding,
    CapabilityDefinition,
    CapabilityVersion,
    Connector,
)
from obsion.domain.enums import (
    ActionApprovalPurpose,
    ActorType,
    ApprovalStatus,
    CapabilityTransport,
    ConnectorStatus,
    DecisionEffect,
    RegistryStatus,
    RiskLevel,
    SideEffect,
)
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore
from obsion.security.identity import Principal
from obsion.security.policy import ActionPolicyInput, PolicyEngine
from obsion.security.redaction import redact
from obsion.telemetry import action_counter, tracer


class ActionGatewayStatus(StrEnum):
    COMPLETED = "COMPLETED"
    DENIED = "DENIED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ResolvedActionCapability:
    definition: CapabilityDefinition
    version: CapabilityVersion
    binding: CapabilityBinding
    connector: Connector

    def plan_reference(self) -> dict[str, Any]:
        return {
            "capability_name": self.definition.name,
            "capability_version_id": str(self.version.id),
            "version": self.version.version,
            "checksum_sha256": self.version.checksum_sha256,
            "permission_action": self.version.permission_action,
            "risk_level": self.version.risk_level.value,
            "side_effect": self.version.side_effect.value,
            "connector_id": str(self.connector.id),
            "binding_id": str(self.binding.id),
        }


@dataclass(frozen=True, slots=True)
class ActionGatewayRequest:
    principal: Principal
    action: ActionRequest
    approval: ActionApproval
    attempt: ActionAttempt
    plan_checksum_sha256: str
    purpose: ActionApprovalPurpose
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ActionGatewayResult:
    status: ActionGatewayStatus
    policy_decision_id: UUID
    output: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        validate_error_code(self.error_code)


def action_provider_payload(
    action: ActionRequest,
    *,
    plan_checksum_sha256: str,
    purpose: ActionApprovalPurpose,
    parameters: dict[str, Any],
    original_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action_type": action.action_type.value,
        "purpose": purpose.value,
        "target": action.target,
        "parameters": parameters,
        "obsion": {
            "action_request_id": str(action.id),
            "plan_checksum_sha256": plan_checksum_sha256,
        },
    }
    if purpose == ActionApprovalPurpose.ROLLBACK:
        payload["original_output"] = original_output or {}
    return payload


class ActionGateway:
    """Mandatory execution boundary for the closed Phase 7 write surface."""

    def __init__(
        self,
        *,
        policy: PolicyEngine | None = None,
        credentials: CredentialBroker | None = None,
        events: EventStore | None = None,
        audit: AuditWriter | None = None,
        rate_limiter: CapabilityRateLimiter | None = None,
        max_response_bytes: int = 1_048_576,
    ) -> None:
        self.policy = policy or PolicyEngine()
        self.credentials = credentials or CredentialBroker()
        self.events = events or EventStore()
        self.audit = audit or AuditWriter()
        self.rate_limiter = rate_limiter or InMemoryFixedWindowRateLimiter(60)
        self.max_response_bytes = max_response_bytes

    async def preflight(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        capability_name: str,
        environment: str,
        resource: dict[str, Any],
    ) -> ResolvedActionCapability:
        rows = (
            await session.execute(
                select(
                    CapabilityDefinition,
                    CapabilityVersion,
                    CapabilityBinding,
                    Connector,
                )
                .join(
                    CapabilityVersion,
                    CapabilityVersion.capability_id == CapabilityDefinition.id,
                )
                .join(
                    CapabilityBinding,
                    CapabilityBinding.capability_version_id == CapabilityVersion.id,
                )
                .join(Connector, Connector.id == CapabilityBinding.connector_id)
                .where(
                    CapabilityDefinition.organization_id == principal.organization_id,
                    CapabilityDefinition.name == capability_name,
                    CapabilityDefinition.status == RegistryStatus.ACTIVE,
                    CapabilityBinding.organization_id == principal.organization_id,
                    CapabilityBinding.environment == environment,
                    CapabilityBinding.enabled.is_(True),
                    Connector.organization_id == principal.organization_id,
                    Connector.environment == environment,
                    Connector.status == ConnectorStatus.ACTIVE,
                )
                .order_by(CapabilityVersion.version.desc())
            )
        ).all()
        row = next(
            (
                candidate
                for candidate in rows
                if self._selector_matches(candidate[2].resource_selector, resource)
            ),
            None,
        )
        if row is None:
            raise NotFoundError("Action capability binding", capability_name)
        resolved = ResolvedActionCapability(*row._tuple())
        self._validate_contract(resolved, principal, environment)
        self._validate_endpoint(resolved.connector)
        return resolved

    async def invoke(
        self, session: AsyncSession, request: ActionGatewayRequest
    ) -> ActionGatewayResult:
        with tracer.start_as_current_span("obsion.action.invoke") as span:
            span.set_attribute("obsion.action.id", str(request.action.id))
            span.set_attribute("obsion.action.purpose", request.purpose.value)
            result = await self._invoke(session, request)
            span.set_attribute("obsion.action.status", result.status.value)
            action_counter.add(
                1,
                {
                    "type": request.action.action_type.value,
                    "purpose": request.purpose.value,
                    "status": result.status.value,
                },
            )
            return result

    def validate_preflight_payload(
        self,
        resolved: ResolvedActionCapability,
        payload: dict[str, Any],
    ) -> None:
        self._validate_schema(
            resolved.version.input_schema,
            payload,
            "action_input_invalid",
        )

    async def _invoke(
        self, session: AsyncSession, request: ActionGatewayRequest
    ) -> ActionGatewayResult:
        resolved = await self._resolve_pinned(session, request)
        approval_valid = (
            request.approval.organization_id == request.principal.organization_id
            and request.approval.action_request_id == request.action.id
            and request.approval.purpose == request.purpose
            and request.approval.status == ApprovalStatus.APPROVED
            and request.approval.plan_checksum_sha256 == request.plan_checksum_sha256
            and ensure_utc(request.approval.expires_at) > utc_now()
        )
        decision = await self.policy.evaluate_action(
            session,
            ActionPolicyInput(
                principal=request.principal,
                capability=resolved.version,
                action=resolved.version.permission_action,
                resource=request.action.target,
                context={
                    "environment": request.action.environment,
                    "action_type": request.action.action_type.value,
                    "purpose": request.purpose.value,
                    "plan_checksum_sha256": request.plan_checksum_sha256,
                },
                action_request_id=request.action.id,
                approval_valid=approval_valid,
            ),
        )
        request.attempt.policy_decision_id = decision.id
        await session.flush()
        await self.events.append(
            session,
            EventDraft(
                name="action.policy_decided",
                aggregate_type="action_request",
                aggregate_id=request.action.id,
                organization_id=request.principal.organization_id,
                correlation_id=request.action.id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                payload={
                    "decision_id": str(decision.id),
                    "effect": decision.effect.value,
                    "reasons": list(decision.reason_codes),
                    "purpose": request.purpose.value,
                },
            ),
        )
        if decision.effect == DecisionEffect.DENY:
            await self._audit(
                session, request, resolved, decision.id, "DENIED", decision.reason_codes
            )
            return ActionGatewayResult(
                status=ActionGatewayStatus.DENIED,
                policy_decision_id=decision.id,
                error_code="action_policy_denied",
                error_message="The action was denied by policy",
            )

        try:
            self._validate_schema(
                resolved.version.input_schema,
                request.payload,
                "action_input_invalid",
            )
        except ValidationError as exc:
            await self._audit(session, request, resolved, decision.id, "FAILED", (exc.code,))
            return ActionGatewayResult(
                status=ActionGatewayStatus.FAILED,
                policy_decision_id=decision.id,
                error_code=exc.code,
                error_message=exc.message,
            )

        rate_key = ":".join(
            (
                str(request.principal.organization_id),
                str(request.principal.id),
                str(resolved.version.id),
                str(resolved.connector.id),
            )
        )
        configured_limit = resolved.connector.configuration.get("rate_limit_per_minute")
        limit = (
            configured_limit
            if isinstance(configured_limit, int) and not isinstance(configured_limit, bool)
            else None
        )
        try:
            allowed = await self.rate_limiter.allow(rate_key, limit)
        except RateLimitUnavailable:
            allowed = False
        if not allowed:
            await self._audit(
                session,
                request,
                resolved,
                decision.id,
                "DENIED",
                ("action_rate_limited",),
            )
            return ActionGatewayResult(
                status=ActionGatewayStatus.DENIED,
                policy_decision_id=decision.id,
                error_code="action_rate_limited",
                error_message="The action provider rate limit has been reached",
            )

        started = perf_counter()
        await self.events.append(
            session,
            EventDraft(
                name="action.provider_started",
                aggregate_type="action_request",
                aggregate_id=request.action.id,
                organization_id=request.principal.organization_id,
                correlation_id=request.action.id,
                actor_type=ActorType.SERVICE,
                actor_id=request.principal.id,
                payload={
                    "attempt_id": str(request.attempt.id),
                    "capability": resolved.definition.name,
                    "connector": resolved.connector.name,
                    "purpose": request.purpose.value,
                },
            ),
        )
        try:
            output = await self._post(session, resolved, request)
            self._validate_schema(
                resolved.version.output_schema,
                output,
                "action_output_invalid",
            )
            safe_output = redact(output)
            latency_ms = int((perf_counter() - started) * 1000)
            await self.events.append(
                session,
                EventDraft(
                    name="action.provider_completed",
                    aggregate_type="action_request",
                    aggregate_id=request.action.id,
                    organization_id=request.principal.organization_id,
                    correlation_id=request.action.id,
                    actor_type=ActorType.SERVICE,
                    actor_id=request.principal.id,
                    payload={
                        "attempt_id": str(request.attempt.id),
                        "purpose": request.purpose.value,
                        "latency_ms": latency_ms,
                    },
                ),
            )
            await self._audit(
                session,
                request,
                resolved,
                decision.id,
                "SUCCESS",
                (),
                latency_ms=latency_ms,
            )
            return ActionGatewayResult(
                status=ActionGatewayStatus.COMPLETED,
                policy_decision_id=decision.id,
                output=safe_output,
            )
        except Exception as exc:
            latency_ms = int((perf_counter() - started) * 1000)
            if isinstance(exc, ValidationError):
                error_code = exc.code
                error_message = exc.message
            elif isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                error_code = "action_provider_timeout"
                error_message = "The action provider timed out"
            else:
                error_code = "action_provider_failed"
                error_message = "The action provider could not complete the request"
            await self.events.append(
                session,
                EventDraft(
                    name="action.provider_failed",
                    aggregate_type="action_request",
                    aggregate_id=request.action.id,
                    organization_id=request.principal.organization_id,
                    correlation_id=request.action.id,
                    actor_type=ActorType.SERVICE,
                    actor_id=request.principal.id,
                    payload={
                        "attempt_id": str(request.attempt.id),
                        "purpose": request.purpose.value,
                        "error_code": error_code,
                    },
                ),
            )
            await self._audit(
                session,
                request,
                resolved,
                decision.id,
                "FAILED",
                (error_code,),
                latency_ms=latency_ms,
            )
            return ActionGatewayResult(
                status=ActionGatewayStatus.FAILED,
                policy_decision_id=decision.id,
                error_code=error_code,
                error_message=error_message,
            )

    async def _resolve_pinned(
        self, session: AsyncSession, request: ActionGatewayRequest
    ) -> ResolvedActionCapability:
        row = (
            await session.execute(
                select(
                    CapabilityDefinition,
                    CapabilityVersion,
                    CapabilityBinding,
                    Connector,
                )
                .join(
                    CapabilityVersion,
                    CapabilityVersion.capability_id == CapabilityDefinition.id,
                )
                .join(
                    CapabilityBinding,
                    CapabilityBinding.capability_version_id == CapabilityVersion.id,
                )
                .join(Connector, Connector.id == CapabilityBinding.connector_id)
                .where(
                    CapabilityVersion.id == request.attempt.capability_version_id,
                    CapabilityDefinition.organization_id == request.principal.organization_id,
                    CapabilityDefinition.status == RegistryStatus.ACTIVE,
                    CapabilityBinding.connector_id == request.attempt.connector_id,
                    CapabilityBinding.environment == request.action.environment,
                    CapabilityBinding.enabled.is_(True),
                    Connector.organization_id == request.principal.organization_id,
                    Connector.environment == request.action.environment,
                    Connector.status == ConnectorStatus.ACTIVE,
                )
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("Pinned action capability", request.attempt.capability_version_id)
        resolved = ResolvedActionCapability(*row._tuple())
        self._validate_contract(resolved, request.principal, request.action.environment)
        self._validate_endpoint(resolved.connector)
        return resolved

    async def _post(
        self,
        session: AsyncSession,
        resolved: ResolvedActionCapability,
        request: ActionGatewayRequest,
    ) -> dict[str, Any]:
        endpoint = resolved.connector.endpoint
        if endpoint is None:
            raise ValidationError("connector_endpoint_missing", "Action connector has no endpoint")
        credential = await self.credentials.resolve(
            resolved.connector.credential_ref,
            session=session,
            organization_id=request.principal.organization_id,
        )
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Idempotency-Key": request.attempt.idempotency_key,
            "X-Obsion-Action-ID": str(request.action.id),
            "X-Obsion-Action-Purpose": request.purpose.value,
        }
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        timeout_seconds = min(
            resolved.version.timeout_seconds,
            int(resolved.connector.configuration.get("timeout_seconds", 120)),
        )
        try:
            async with httpx.AsyncClient(
                timeout=timeout_seconds,
                follow_redirects=False,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            ) as client:
                response = await asyncio.wait_for(
                    client.post(endpoint, json=request.payload, headers=headers),
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length is not None and int(content_length) > self.max_response_bytes:
                    raise ValidationError(
                        "action_response_too_large", "Action provider response is too large"
                    )
                if len(response.content) > self.max_response_bytes:
                    raise ValidationError(
                        "action_response_too_large", "Action provider response is too large"
                    )
                output = response.json()
        finally:
            del credential
        if not isinstance(output, dict):
            raise ValidationError(
                "action_output_invalid", "Action provider response must be a JSON object"
            )
        return output

    @staticmethod
    def _validate_contract(
        resolved: ResolvedActionCapability,
        principal: Principal,
        environment: str,
    ) -> None:
        if environment not in {"development", "staging"}:
            raise ValidationError(
                "v1_production_action_boundary",
                "V1 actions are limited to development and staging",
            )
        version = resolved.version
        if (
            version.risk_level != RiskLevel.L3
            or version.side_effect != SideEffect.IDEMPOTENT_WRITE
            or version.transport != CapabilityTransport.HTTP
        ):
            raise ValidationError(
                "v1_action_capability_boundary",
                "V1 actions require an L3 idempotent HTTP write capability",
            )
        if not principal.can("action.execute") or not principal.can(version.permission_action):
            raise ValidationError(
                "action_permission_missing",
                "The action owner lacks the required execution permission",
            )
        if version.permission_action not in resolved.connector.declared_grants:
            raise ValidationError(
                "connector_grant_missing",
                "The connector has not declared the capability permission",
            )

    @staticmethod
    def _validate_endpoint(connector: Connector) -> None:
        endpoint = connector.endpoint
        if not endpoint:
            raise ValidationError("connector_endpoint_missing", "Action connector has no endpoint")
        parsed = urlparse(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValidationError(
                "connector_egress_invalid", "Action connector endpoint is invalid"
            )
        authority = _endpoint_authority(endpoint)
        try:
            allowed = {
                _endpoint_authority(item, default_scheme=parsed.scheme)
                for item in connector.allowed_egress
                if isinstance(item, str)
            }
        except ValueError as exc:
            raise ValidationError(
                "connector_egress_invalid", "Action connector egress configuration is invalid"
            ) from exc
        if authority not in allowed:
            raise ValidationError(
                "connector_egress_denied", "Action connector endpoint is outside its allowlist"
            )
        if parsed.scheme != "https" and connector.environment != "development":
            raise ValidationError(
                "connector_tls_required", "Non-development action connectors must use TLS"
            )

    @staticmethod
    def _selector_matches(selector: dict[str, Any], resource: dict[str, Any]) -> bool:
        for key, expected in selector.items():
            current: Any = resource
            for part in key.split("."):
                if not isinstance(current, dict) or part not in current:
                    return False
                current = current[part]
            if current != expected:
                return False
        return True

    @staticmethod
    def _validate_schema(schema: dict[str, Any], payload: dict[str, Any], code: str) -> None:
        try:
            Draft202012Validator(
                schema,
                format_checker=Draft202012Validator.FORMAT_CHECKER,
            ).validate(payload)
        except JsonSchemaError as exc:
            path = ".".join(str(part) for part in exc.absolute_path)
            raise ValidationError(
                code, "Action payload does not match its capability schema", path=path
            ) from exc

    async def _audit(
        self,
        session: AsyncSession,
        request: ActionGatewayRequest,
        resolved: ResolvedActionCapability,
        policy_decision_id: UUID,
        outcome: str,
        reasons: tuple[str, ...],
        *,
        latency_ms: int | None = None,
    ) -> None:
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=request.principal.organization_id,
                correlation_id=request.action.id,
                actor_type=ActorType.SERVICE,
                actor_id=request.principal.id,
                action=resolved.version.permission_action,
                resource_type="action_request",
                resource_id=str(request.action.id),
                outcome=outcome,
                risk_level=resolved.version.risk_level,
                policy_decision_id=policy_decision_id,
                metadata={
                    "attempt_id": str(request.attempt.id),
                    "purpose": request.purpose.value,
                    "reasons": list(reasons),
                },
                latency_ms=latency_ms,
            ),
        )


def _endpoint_authority(value: str, *, default_scheme: str = "https") -> tuple[str, int]:
    candidate = value if "://" in value else f"{default_scheme}://{value}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("invalid HTTP authority")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.hostname.casefold(), port
