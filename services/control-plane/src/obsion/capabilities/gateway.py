import asyncio
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from time import perf_counter
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.capabilities.connectors import ConnectorContext, ConnectorExecutor, CredentialBroker
from obsion.capabilities.rate_limit import (
    CapabilityRateLimiter,
    InMemoryFixedWindowRateLimiter,
    RateLimitUnavailable,
)
from obsion.common.errors import NotFoundError, ValidationError
from obsion.common.time import utc_now
from obsion.db.models import (
    Approval,
    CapabilityBinding,
    CapabilityDefinition,
    CapabilityVersion,
    Connector,
    Evidence,
    PolicyDecision,
)
from obsion.domain.enums import (
    ActorType,
    ApprovalStatus,
    ConnectorStatus,
    DecisionEffect,
    EvidenceType,
    RegistryStatus,
)
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore
from obsion.security.identity import Principal
from obsion.security.masking import apply_obligations
from obsion.security.policy import Decision, PolicyEngine, PolicyInput
from obsion.security.redaction import redact
from obsion.telemetry import capability_counter, tracer


class GatewayStatus(StrEnum):
    COMPLETED = "COMPLETED"
    DENIED = "DENIED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class GatewayRequest:
    principal: Principal
    capability_name: str
    payload: dict[str, Any]
    resource: dict[str, Any]
    environment: str
    agent_name: str
    run_id: UUID
    step_id: UUID | None = None
    agent_version_id: UUID | None = None
    capability_version: int | None = None
    capability_version_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class GatewayResult:
    status: GatewayStatus
    policy_decision_id: UUID
    output: dict[str, Any] | None = None
    evidence_id: UUID | None = None
    approval_id: UUID | None = None
    error_code: str | None = None
    error_message: str | None = None
    capability_version_id: UUID | None = None
    connector_id: UUID | None = None


class CapabilityGateway:
    def __init__(
        self,
        executors: dict[str, ConnectorExecutor],
        *,
        policy: PolicyEngine | None = None,
        credentials: CredentialBroker | None = None,
        events: EventStore | None = None,
        audit: AuditWriter | None = None,
        rate_limiter: CapabilityRateLimiter | None = None,
    ) -> None:
        self.executors = executors
        self.policy = policy or PolicyEngine()
        self.credentials = credentials or CredentialBroker()
        self.events = events or EventStore()
        self.audit = audit or AuditWriter()
        self.rate_limiter = rate_limiter or InMemoryFixedWindowRateLimiter(120)

    async def invoke(self, session: AsyncSession, request: GatewayRequest) -> GatewayResult:
        with tracer.start_as_current_span("obsion.capability.invoke") as span:
            span.set_attribute("obsion.capability.name", request.capability_name)
            span.set_attribute("obsion.run.id", str(request.run_id))
            result = await self._invoke(session, request)
            span.set_attribute("obsion.capability.status", result.status.value)
            capability_counter.add(
                1,
                {"capability": request.capability_name, "status": result.status.value},
            )
            return result

    async def _invoke(self, session: AsyncSession, request: GatewayRequest) -> GatewayResult:
        definition, version, connector = await self._resolve(session, request)
        await self.events.append(
            session,
            EventDraft(
                name="capability.requested",
                aggregate_type="run",
                aggregate_id=request.run_id,
                organization_id=request.principal.organization_id,
                correlation_id=request.run_id,
                actor_type=ActorType.AGENT,
                actor_id=request.agent_version_id,
                run_id=request.run_id,
                payload={
                    "capability": definition.name,
                    "version": version.version,
                    "resource": redact(request.resource),
                },
            ),
        )
        decision = await self.policy.evaluate(
            session,
            PolicyInput(
                principal=request.principal,
                capability=version,
                action=version.permission_action,
                resource=request.resource,
                context={"environment": request.environment},
                agent_name=request.agent_name,
                agent_version_id=request.agent_version_id,
                run_id=request.run_id,
            ),
        )
        await self._policy_event(session, request, decision)
        try:
            self._validate(version.input_schema, request.payload, "capability_input_invalid")
        except ValidationError as exc:
            await self._gateway_event(session, request, "capability.input_rejected")
            await self._audit(
                session,
                request,
                version,
                decision,
                "FAILED",
                metadata={"error_code": exc.code},
            )
            return GatewayResult(
                status=GatewayStatus.FAILED,
                policy_decision_id=decision.id,
                error_code=exc.code,
                error_message=exc.message,
                capability_version_id=version.id,
                connector_id=connector.id,
            )
        if decision.effect == DecisionEffect.DENY:
            await self._audit(
                session,
                request,
                version,
                decision,
                "DENIED",
                metadata={"reasons": decision.reason_codes},
            )
            return GatewayResult(
                status=GatewayStatus.DENIED,
                policy_decision_id=decision.id,
                error_code="capability_denied",
                error_message="The capability request was denied by policy",
                capability_version_id=version.id,
                connector_id=connector.id,
            )

        if decision.effect == DecisionEffect.ASK:
            approved = await self._find_approval(session, request, decision)
            if approved is None:
                approval = await self._create_approval(session, request, decision)
                await self._audit(session, request, version, decision, "WAITING_APPROVAL")
                return GatewayResult(
                    status=GatewayStatus.WAITING_APPROVAL,
                    policy_decision_id=decision.id,
                    approval_id=approval.id,
                    capability_version_id=version.id,
                    connector_id=connector.id,
                )

        rate_key = ":".join(
            (
                str(request.principal.organization_id),
                str(request.principal.id),
                str(version.id),
                str(connector.id),
            )
        )
        configured_limit = connector.configuration.get("rate_limit_per_minute")
        limit = (
            configured_limit
            if isinstance(configured_limit, int) and not isinstance(configured_limit, bool)
            else None
        )
        try:
            rate_allowed = await self.rate_limiter.allow(rate_key, limit)
        except RateLimitUnavailable:
            await self._gateway_event(session, request, "capability.rate_limit_unavailable")
            await self._audit(
                session,
                request,
                version,
                decision,
                "FAILED",
                metadata={"error_code": "rate_limit_unavailable"},
            )
            return GatewayResult(
                status=GatewayStatus.FAILED,
                policy_decision_id=decision.id,
                error_code="rate_limit_unavailable",
                error_message="The capability safety service is temporarily unavailable",
                capability_version_id=version.id,
                connector_id=connector.id,
            )
        if not rate_allowed:
            await self._gateway_event(session, request, "capability.rate_limited")
            await self._audit(
                session,
                request,
                version,
                decision,
                "DENIED",
                metadata={"error_code": "capability_rate_limited"},
            )
            return GatewayResult(
                status=GatewayStatus.DENIED,
                policy_decision_id=decision.id,
                error_code="capability_rate_limited",
                error_message="The capability rate limit has been reached",
                capability_version_id=version.id,
                connector_id=connector.id,
            )

        executor = self.executors.get(version.transport.value)
        if executor is None:
            await self._audit(session, request, version, decision, "FAILED")
            return GatewayResult(
                status=GatewayStatus.FAILED,
                policy_decision_id=decision.id,
                error_code="capability_transport_unavailable",
                error_message="No executor is installed for the capability transport",
                capability_version_id=version.id,
                connector_id=connector.id,
            )
        started = perf_counter()
        await self.events.append(
            session,
            EventDraft(
                name="tool.started",
                aggregate_type="run",
                aggregate_id=request.run_id,
                organization_id=request.principal.organization_id,
                correlation_id=request.run_id,
                actor_type=ActorType.AGENT,
                actor_id=request.agent_version_id,
                run_id=request.run_id,
                payload={"capability": definition.name, "connector": connector.name},
            ),
        )
        try:
            credential = await self.credentials.resolve(
                connector.credential_ref,
                session=session,
                organization_id=request.principal.organization_id,
            )
            result = await asyncio.wait_for(
                executor.invoke(
                    connector,
                    request.payload,
                    credential,
                    ConnectorContext(
                        principal=request.principal,
                        run_id=request.run_id,
                        step_id=request.step_id,
                    ),
                ),
                timeout=version.timeout_seconds,
            )
            del credential
            self._validate(version.output_schema, result.data, "capability_output_invalid")
            output = apply_obligations(result.data, decision.obligations)
            evidence = await self._evidence(
                session,
                request,
                version,
                connector,
                decision.id,
                output,
                result.source,
                result.resource,
                result.observed_at,
            )
            latency_ms = int((perf_counter() - started) * 1000)
            await self.events.append(
                session,
                EventDraft(
                    name="tool.completed",
                    aggregate_type="run",
                    aggregate_id=request.run_id,
                    organization_id=request.principal.organization_id,
                    correlation_id=request.run_id,
                    actor_type=ActorType.AGENT,
                    actor_id=request.agent_version_id,
                    run_id=request.run_id,
                    payload={
                        "capability": definition.name,
                        "connector": connector.name,
                        "latency_ms": latency_ms,
                        "evidence_id": str(evidence.id),
                    },
                ),
            )
            await self._audit(
                session,
                request,
                version,
                decision,
                "SUCCESS",
                latency_ms=latency_ms,
                metadata={"evidence_id": str(evidence.id)},
            )
            return GatewayResult(
                status=GatewayStatus.COMPLETED,
                policy_decision_id=decision.id,
                output=output,
                evidence_id=evidence.id,
                capability_version_id=version.id,
                connector_id=connector.id,
            )
        except Exception as exc:
            latency_ms = int((perf_counter() - started) * 1000)
            error_code = (
                "capability_timeout" if isinstance(exc, TimeoutError) else "capability_failed"
            )
            await self.events.append(
                session,
                EventDraft(
                    name="tool.failed",
                    aggregate_type="run",
                    aggregate_id=request.run_id,
                    organization_id=request.principal.organization_id,
                    correlation_id=request.run_id,
                    actor_type=ActorType.AGENT,
                    actor_id=request.agent_version_id,
                    run_id=request.run_id,
                    payload={"capability": definition.name, "error_code": error_code},
                ),
            )
            await self._audit(
                session,
                request,
                version,
                decision,
                "FAILED",
                latency_ms=latency_ms,
                metadata={"error_code": error_code},
            )
            return GatewayResult(
                status=GatewayStatus.FAILED,
                policy_decision_id=decision.id,
                error_code=error_code,
                error_message="The connector could not complete the request",
                capability_version_id=version.id,
                connector_id=connector.id,
            )

    async def _resolve(
        self, session: AsyncSession, request: GatewayRequest
    ) -> tuple[CapabilityDefinition, CapabilityVersion, Connector]:
        if request.capability_version is not None and request.capability_version_id is not None:
            raise ValidationError(
                "capability_version_ambiguous",
                "A capability request may pin either a version number or a version ID",
            )
        statement = (
            select(CapabilityDefinition, CapabilityVersion, CapabilityBinding, Connector)
            .join(CapabilityVersion, CapabilityVersion.capability_id == CapabilityDefinition.id)
            .join(
                CapabilityBinding,
                CapabilityBinding.capability_version_id == CapabilityVersion.id,
            )
            .join(Connector, Connector.id == CapabilityBinding.connector_id)
            .where(
                CapabilityDefinition.organization_id == request.principal.organization_id,
                CapabilityDefinition.name == request.capability_name,
                CapabilityDefinition.status == RegistryStatus.ACTIVE,
                CapabilityBinding.organization_id == request.principal.organization_id,
                CapabilityBinding.environment == request.environment,
                CapabilityBinding.enabled.is_(True),
                Connector.organization_id == request.principal.organization_id,
                Connector.status == ConnectorStatus.ACTIVE,
            )
            .order_by(CapabilityVersion.version.desc())
        )
        if request.capability_version is not None:
            statement = statement.where(CapabilityVersion.version == request.capability_version)
        if request.capability_version_id is not None:
            statement = statement.where(CapabilityVersion.id == request.capability_version_id)
        rows = (await session.execute(statement)).all()
        row = next(
            (
                candidate
                for candidate in rows
                if self._selector_matches(candidate[2].resource_selector, request.resource)
            ),
            None,
        )
        if row is None:
            raise NotFoundError("Capability binding", request.capability_name)
        definition, version, _, connector = row._tuple()
        return definition, version, connector

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
    def _validate(schema: dict[str, Any], payload: dict[str, Any], code: str) -> None:
        try:
            Draft202012Validator(schema).validate(payload)
        except JsonSchemaError as exc:
            path = ".".join(str(part) for part in exc.absolute_path)
            raise ValidationError(
                code, "Capability payload does not match its schema", path=path
            ) from exc

    async def _policy_event(
        self, session: AsyncSession, request: GatewayRequest, decision: Decision
    ) -> None:
        await self.events.append(
            session,
            EventDraft(
                name="policy.decided",
                aggregate_type="run",
                aggregate_id=request.run_id,
                organization_id=request.principal.organization_id,
                correlation_id=request.run_id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                run_id=request.run_id,
                payload={
                    "decision_id": str(decision.id),
                    "effect": decision.effect,
                    "reason_codes": decision.reason_codes,
                },
            ),
        )

    async def _gateway_event(
        self, session: AsyncSession, request: GatewayRequest, name: str
    ) -> None:
        await self.events.append(
            session,
            EventDraft(
                name=name,
                aggregate_type="run",
                aggregate_id=request.run_id,
                organization_id=request.principal.organization_id,
                correlation_id=request.run_id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                run_id=request.run_id,
                payload={"capability": request.capability_name},
            ),
        )

    async def _create_approval(
        self, session: AsyncSession, request: GatewayRequest, decision: Decision
    ) -> Approval:
        token = secrets.token_urlsafe(32)
        approval = Approval(
            organization_id=request.principal.organization_id,
            run_id=request.run_id,
            step_id=request.step_id,
            policy_decision_id=decision.id,
            status=ApprovalStatus.PENDING,
            reason="Policy requires human approval for this sensitive read",
            requested_by=request.principal.id,
            approver_constraints={"permission": "approval.decide", "disallow_self": True},
            expires_at=utc_now() + timedelta(minutes=30),
            resume_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        session.add(approval)
        await session.flush()
        await self.events.append(
            session,
            EventDraft(
                name="approval.requested",
                aggregate_type="run",
                aggregate_id=request.run_id,
                organization_id=request.principal.organization_id,
                correlation_id=request.run_id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                run_id=request.run_id,
                payload={"approval_id": str(approval.id), "expires_at": approval.expires_at},
            ),
        )
        return approval

    async def _find_approval(
        self, session: AsyncSession, request: GatewayRequest, decision: Decision
    ) -> Approval | None:
        current = await session.get(PolicyDecision, decision.id)
        if current is None:
            return None
        statement = (
            select(Approval)
            .join(PolicyDecision, Approval.policy_decision_id == PolicyDecision.id)
            .where(
                Approval.organization_id == request.principal.organization_id,
                Approval.run_id == request.run_id,
                Approval.step_id == request.step_id,
                Approval.status == ApprovalStatus.APPROVED,
                PolicyDecision.input_fingerprint == current.input_fingerprint,
            )
            .order_by(Approval.decided_at.desc())
        )
        approval = await session.scalar(statement.limit(1).with_for_update())
        if approval is not None and approval.resume_token_used_at is None:
            approval.resume_token_used_at = utc_now()
            return approval
        return None

    async def _evidence(
        self,
        session: AsyncSession,
        request: GatewayRequest,
        version: CapabilityVersion,
        connector: Connector,
        policy_decision_id: UUID,
        output: dict[str, Any],
        source: str,
        resource: str,
        observed_at: datetime | None,
    ) -> Evidence:
        serialized = json.dumps(output, sort_keys=True, separators=(",", ":"), default=str)
        mapping_type = version.evidence_mapping.get("type", "TOOL")
        try:
            evidence_type = EvidenceType(mapping_type)
        except ValueError:
            evidence_type = EvidenceType.TOOL
        evidence = Evidence(
            organization_id=request.principal.organization_id,
            run_id=request.run_id,
            step_id=request.step_id,
            evidence_type=evidence_type,
            source=source,
            resource=resource,
            observed_at=observed_at or utc_now(),
            ingested_at=utc_now(),
            content=redact(output),
            content_fingerprint=hashlib.sha256(serialized.encode()).hexdigest(),
            confidence=version.evidence_mapping.get("confidence", 1.0),
            classification=version.data_classification,
            permissions=[version.permission_action],
            lineage={
                "capability_version_id": str(version.id),
                "connector_id": str(connector.id),
                "policy_decision_id": str(policy_decision_id),
            },
        )
        session.add(evidence)
        await session.flush()
        await self.events.append(
            session,
            EventDraft(
                name="evidence.created",
                aggregate_type="run",
                aggregate_id=request.run_id,
                organization_id=request.principal.organization_id,
                correlation_id=request.run_id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                run_id=request.run_id,
                payload={"evidence_id": str(evidence.id), "type": evidence.evidence_type},
            ),
        )
        return evidence

    async def _audit(
        self,
        session: AsyncSession,
        request: GatewayRequest,
        version: CapabilityVersion,
        decision: Decision,
        outcome: str,
        *,
        latency_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=request.principal.organization_id,
                correlation_id=request.run_id,
                actor_type=ActorType.AGENT,
                actor_id=request.agent_version_id,
                action=version.permission_action,
                resource_type="capability",
                resource_id=str(version.id),
                outcome=outcome,
                risk_level=version.risk_level,
                policy_decision_id=decision.id,
                metadata=metadata or {},
                latency_ms=latency_ms,
            ),
        )
