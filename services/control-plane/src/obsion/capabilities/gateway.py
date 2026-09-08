import asyncio
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from time import perf_counter
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.capabilities.codeup import (
    catalog_allowed,
    is_codeup_catalog_connector,
    is_codeup_connector,
    repository_allowed,
)
from obsion.capabilities.codeup_contract import (
    CODEUP_ALL_OPERATIONS,
    CODEUP_DISCOVERY_OPERATION,
    CODEUP_OPERATIONS,
)
from obsion.capabilities.connectors import ConnectorContext, ConnectorExecutor, CredentialBroker
from obsion.capabilities.dingtalk_robot import (
    DINGTALK_ROBOT_CAPABILITY,
    DINGTALK_ROBOT_PROTOCOL,
    DingTalkRobotTransport,
    RobotCredentials,
    RobotQueryResult,
    RobotQueryState,
    RobotSendResult,
    RobotSendState,
    connector_fingerprint,
)
from obsion.capabilities.rate_limit import (
    CapabilityRateLimiter,
    InMemoryFixedWindowRateLimiter,
    RateLimitUnavailable,
)
from obsion.capabilities.vendor_knowledge import VENDOR_KNOWLEDGE_BROWSE_OPERATIONS
from obsion.common.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ObsionError,
    ValidationError,
)
from obsion.common.time import utc_now
from obsion.contracts.errors import validate_error_code
from obsion.db.models import (
    AgentVersion,
    Approval,
    CapabilityBinding,
    CapabilityDefinition,
    CapabilityVersion,
    Connector,
    Evidence,
    OperatorCapabilityInvocation,
    PolicyDecision,
)
from obsion.domain.enums import (
    ActorType,
    ApprovalStatus,
    Classification,
    ConnectorStatus,
    DecisionEffect,
    EvidenceType,
    RegistryStatus,
    RiskLevel,
    SideEffect,
)
from obsion.domains.evidence.fabric import EvidenceFabric, EvidenceInput
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore
from obsion.persistence.operator_invocations import (
    OperatorInvocationStore,
    operator_request_fingerprint,
)
from obsion.registry.agent_spec import sandbox_allows_capabilities
from obsion.security.identity import Principal
from obsion.security.masking import apply_obligations
from obsion.security.policy import Decision, PolicyEngine, PolicyInput, ResourcePolicyInput
from obsion.security.redaction import redact
from obsion.telemetry import capability_counter, capability_duration, tracer


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
    model_profile_id: UUID | None = None
    capability_version: int | None = None
    capability_version_id: UUID | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OperatorGatewayRequest:
    """Capability invocation from an authenticated control-plane operation.

    Operator source management has no Harness Run, so it cannot emit Run Events,
    create Run-scoped Evidence, or request a Run approval.  It still traverses the
    same capability/connector, Policy, schema, rate, credential, executor, masking,
    and Audit boundary as an Agent invocation.
    """

    principal: Principal
    capability_name: str
    payload: dict[str, Any]
    resource: dict[str, Any]
    environment: str
    correlation_id: UUID
    capability_version: int | None = None
    capability_version_id: UUID | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DingTalkRobotOutboxRequest:
    """A claimed, direct-message Outbox item presented to the Capability Gateway.

    ``text`` is intentionally excluded from ``resource`` and every persisted
    event/audit payload. The request has a Run because the sender's completed
    task is the authorization and observability boundary.
    """

    principal: Principal
    run_id: UUID
    outbox_id: UUID
    installation_id: UUID
    recipient_sender_id: str
    app_key: str
    robot_code: str
    text: str
    connector_id: UUID
    connector_fingerprint: str
    environment: str
    capability_version_id: UUID
    conversation_type: str = "direct"
    recipient_conversation_id: str | None = None


@dataclass(frozen=True, slots=True)
class DingTalkRobotOutboxResult:
    send: RobotSendResult
    policy_decision_id: UUID | None
    capability_version_id: UUID | None
    connector_id: UUID | None


@dataclass(frozen=True, slots=True)
class DingTalkRobotOutboxQueryRequest:
    principal: Principal
    run_id: UUID
    outbox_id: UUID
    installation_id: UUID
    recipient_sender_id: str
    app_key: str
    robot_code: str
    process_query_key: str
    connector_id: UUID
    connector_fingerprint: str
    environment: str
    capability_version_id: UUID
    conversation_type: str = "direct"
    recipient_conversation_id: str | None = None


@dataclass(frozen=True, slots=True)
class DingTalkRobotOutboxQueryResult:
    query: RobotQueryResult
    policy_decision_id: UUID | None
    capability_version_id: UUID | None
    connector_id: UUID | None


def _configured_rate_limit(connector: Connector) -> int | None:
    value = connector.configuration.get("rate_limit_per_minute")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(frozen=True, slots=True)
class OperatorPreparedInvocation:
    version: CapabilityVersion
    connector: Connector
    decision: Decision
    executor: ConnectorExecutor
    idempotent_write: bool


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

    def __post_init__(self) -> None:
        validate_error_code(self.error_code)


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
        operator_invocations: OperatorInvocationStore | None = None,
        operator_idempotency_retention_hours: int = 24,
    ) -> None:
        self.executors = executors
        self.policy = policy or PolicyEngine()
        self.credentials = credentials or CredentialBroker()
        self.events = events or EventStore()
        self.audit = audit or AuditWriter()
        self.rate_limiter = rate_limiter or InMemoryFixedWindowRateLimiter(120)
        self.operator_invocations = operator_invocations or OperatorInvocationStore()
        self.operator_idempotency_retention_hours = max(1, operator_idempotency_retention_hours)
        self.evidence = EvidenceFabric()

    async def invoke(self, session: AsyncSession, request: GatewayRequest) -> GatewayResult:
        with tracer.start_as_current_span("obsion.capability.invoke") as span:
            span.set_attribute("obsion.capability.name", request.capability_name)
            span.set_attribute("obsion.run.id", str(request.run_id))
            started = perf_counter()
            result = await self._invoke(session, request)
            span.set_attribute("obsion.capability.status", result.status.value)
            attributes = {
                "capability": request.capability_name,
                "status": result.status.value,
            }
            capability_counter.add(1, attributes)
            capability_duration.record((perf_counter() - started) * 1000, attributes)
            return result

    async def invoke_operator(
        self,
        session: AsyncSession,
        request: OperatorGatewayRequest,
    ) -> GatewayResult:
        """Invoke a Capability for control-plane source management without a fake Run."""

        with tracer.start_as_current_span("obsion.capability.operator_invoke") as span:
            span.set_attribute("obsion.capability.name", request.capability_name)
            span.set_attribute("obsion.correlation.id", str(request.correlation_id))
            started = perf_counter()
            result = await self._invoke_operator_durable(session, request)
            span.set_attribute("obsion.capability.status", result.status.value)
            attributes = {
                "capability": request.capability_name,
                "status": result.status.value,
                "mode": "operator",
            }
            capability_counter.add(1, attributes)
            capability_duration.record((perf_counter() - started) * 1000, attributes)
            return result

    async def invoke_dingtalk_robot_outbox(
        self,
        session: AsyncSession,
        request: DingTalkRobotOutboxRequest,
        *,
        transport: DingTalkRobotTransport | None = None,
    ) -> DingTalkRobotOutboxResult:
        """Execute a claimed one-to-one robot delivery through the Gateway.

        This is a service-delivery exception to the generic Agent read-only
        path. It still resolves the versioned capability and Connector,
        requires an explicit L2 Policy ALLOW, enforces grants/rate limits,
        resolves credentials through the broker, emits Run events, and audits
        the result. It never retries a vendor message POST.
        """

        resolution = GatewayRequest(
            principal=request.principal,
            capability_name=DINGTALK_ROBOT_CAPABILITY,
            payload={
                "operation": DINGTALK_ROBOT_CAPABILITY,
                "installation_id": str(request.installation_id),
                "recipient_sender_id": request.recipient_sender_id,
                "outbox_id": str(request.outbox_id),
                "conversation_type": request.conversation_type,
                "recipient_conversation_id": request.recipient_conversation_id or "",
            },
            resource={
                "installation_id": str(request.installation_id),
                "recipient_sender_id": request.recipient_sender_id,
                "outbox_id": str(request.outbox_id),
                "channel": "dingtalk",
                "conversation_type": request.conversation_type,
                "conversation_id": request.recipient_conversation_id or request.recipient_sender_id,
            },
            environment=request.environment,
            agent_name="experience-im-outbox",
            run_id=request.run_id,
            capability_version_id=request.capability_version_id,
            context={"entrypoint": "dingtalk-robot-outbox"},
        )
        try:
            definition, version, connector = await self._resolve(session, resolution)
        except ObsionError:
            return DingTalkRobotOutboxResult(
                RobotSendResult(RobotSendState.NOT_ATTEMPTED, reason="capability_unavailable"),
                None,
                None,
                None,
            )
        if not self._dingtalk_robot_contract_valid(definition, version, connector, request):
            return DingTalkRobotOutboxResult(
                RobotSendResult(
                    RobotSendState.NOT_ATTEMPTED, reason="delivery_configuration_invalid"
                ),
                None,
                version.id,
                connector.id,
            )
        decision = await self.policy.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=request.principal,
                action=version.permission_action,
                resource=resolution.resource,
                context={**resolution.context, "environment": request.environment},
                risk_level=version.risk_level,
                resource_type="im_delivery",
                capability_version_id=version.id,
                agent_name="experience-im-outbox",
                run_id=request.run_id,
            ),
        )
        await self._policy_event(session, resolution, decision)
        if decision.effect != DecisionEffect.ALLOW or decision.obligations:
            await self._audit(
                session,
                resolution,
                version,
                decision,
                "DENIED",
                metadata={"error_code": "im_delivery_denied"},
            )
            return DingTalkRobotOutboxResult(
                RobotSendResult(RobotSendState.NOT_ATTEMPTED, reason="delivery_not_authorized"),
                decision.id,
                version.id,
                connector.id,
            )
        if not self._connector_grant_allows(connector, version):
            await self._audit(
                session,
                resolution,
                version,
                decision,
                "DENIED",
                metadata={"error_code": "connector_grant_missing"},
            )
            return DingTalkRobotOutboxResult(
                RobotSendResult(RobotSendState.NOT_ATTEMPTED, reason="connector_grant_missing"),
                decision.id,
                version.id,
                connector.id,
            )
        try:
            self._validate(version.input_schema, resolution.payload, "capability_input_invalid")
            rate_allowed = await self.rate_limiter.allow(
                ":".join(
                    (
                        str(request.principal.organization_id),
                        str(request.principal.id),
                        str(version.id),
                        str(connector.id),
                    )
                ),
                _configured_rate_limit(connector),
            )
        except (RateLimitUnavailable, ObsionError):
            await self._audit(
                session,
                resolution,
                version,
                decision,
                "FAILED",
                metadata={"error_code": "delivery_gateway_unavailable"},
            )
            return DingTalkRobotOutboxResult(
                RobotSendResult(
                    RobotSendState.NOT_ATTEMPTED, reason="delivery_gateway_unavailable"
                ),
                decision.id,
                version.id,
                connector.id,
            )
        if not rate_allowed:
            await self._gateway_event(session, resolution, "capability.rate_limited")
            await self._audit(
                session,
                resolution,
                version,
                decision,
                "DENIED",
                metadata={"error_code": "capability_rate_limited"},
            )
            return DingTalkRobotOutboxResult(
                RobotSendResult(RobotSendState.NOT_ATTEMPTED, reason="capability_rate_limited"),
                decision.id,
                version.id,
                connector.id,
            )

        await self.events.append(
            session,
            EventDraft(
                name="tool.started",
                aggregate_type="run",
                aggregate_id=request.run_id,
                organization_id=request.principal.organization_id,
                correlation_id=request.run_id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                run_id=request.run_id,
                payload={"capability": definition.name, "connector": connector.name},
            ),
        )
        started = perf_counter()
        credential: str | None = None
        try:
            credential = await self.credentials.resolve(
                connector.credential_ref,
                session=session,
                organization_id=request.principal.organization_id,
            )
            if not isinstance(credential, str) or not credential:
                raise ValidationError(
                    "credential_unavailable", "The connector credential is not available"
                )
            robot = transport or DingTalkRobotTransport()
            credentials = RobotCredentials(app_key=request.app_key, app_secret=credential)
            if request.conversation_type == "group":
                if not request.recipient_conversation_id:
                    raise ValueError("Group delivery requires an open conversation id")
                outcome = await robot.send_group_text(
                    credentials,
                    robot_code=request.robot_code,
                    open_conversation_id=request.recipient_conversation_id,
                    text=request.text,
                )
            else:
                outcome = await robot.send_text(
                    credentials,
                    robot_code=request.robot_code,
                    user_id=request.recipient_sender_id,
                    text=request.text,
                )
        except ObsionError:
            outcome = RobotSendResult(RobotSendState.NOT_ATTEMPTED, reason="credential_unavailable")
        except (OSError, TimeoutError, ValueError):
            outcome = RobotSendResult(
                RobotSendState.NOT_ATTEMPTED, reason="delivery_precondition_failed"
            )
        finally:
            credential = None
        event_name = "tool.completed" if outcome.state == RobotSendState.ACCEPTED else "tool.failed"
        await self.events.append(
            session,
            EventDraft(
                name=event_name,
                aggregate_type="run",
                aggregate_id=request.run_id,
                organization_id=request.principal.organization_id,
                correlation_id=request.run_id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                run_id=request.run_id,
                payload={"capability": definition.name, "state": outcome.state},
            ),
        )
        await self._audit(
            session,
            resolution,
            version,
            decision,
            "SUCCESS" if outcome.state == RobotSendState.ACCEPTED else outcome.state,
            latency_ms=int((perf_counter() - started) * 1000),
            metadata={"state": outcome.state, "reason": outcome.reason},
        )
        return DingTalkRobotOutboxResult(outcome, decision.id, version.id, connector.id)

    async def reconcile_dingtalk_robot_outbox(
        self,
        session: AsyncSession,
        request: DingTalkRobotOutboxQueryRequest,
        *,
        transport: DingTalkRobotTransport | None = None,
    ) -> DingTalkRobotOutboxQueryResult:
        """Query one vendor receipt through the same governed delivery boundary.

        This path is deliberately read-only at the vendor boundary. It never
        calls ``batchSend`` and an UNKNOWN query result is returned to the
        durable Outbox caller for conservative persistence.
        """

        resolution = GatewayRequest(
            principal=request.principal,
            capability_name=DINGTALK_ROBOT_CAPABILITY,
            payload={
                "operation": DINGTALK_ROBOT_CAPABILITY,
                "installation_id": str(request.installation_id),
                "recipient_sender_id": request.recipient_sender_id,
                "outbox_id": str(request.outbox_id),
                "conversation_type": request.conversation_type,
                "recipient_conversation_id": request.recipient_conversation_id or "",
            },
            resource={
                "installation_id": str(request.installation_id),
                "recipient_sender_id": request.recipient_sender_id,
                "outbox_id": str(request.outbox_id),
                "channel": "dingtalk",
                "conversation_type": request.conversation_type,
                "conversation_id": request.recipient_conversation_id or request.recipient_sender_id,
                "operation": "reconcile",
            },
            environment=request.environment,
            agent_name="experience-im-outbox-reconciliation",
            run_id=request.run_id,
            capability_version_id=request.capability_version_id,
            context={"entrypoint": "dingtalk-robot-outbox-reconciliation"},
        )
        try:
            definition, version, connector = await self._resolve(session, resolution)
        except ObsionError:
            return DingTalkRobotOutboxQueryResult(
                RobotQueryResult(RobotQueryState.UNKNOWN, reason="capability_unavailable"),
                None,
                None,
                None,
            )
        if not self._dingtalk_robot_contract_valid(definition, version, connector, request):
            return DingTalkRobotOutboxQueryResult(
                RobotQueryResult(RobotQueryState.UNKNOWN, reason="delivery_configuration_invalid"),
                None,
                version.id,
                connector.id,
            )
        decision = await self.policy.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=request.principal,
                action=version.permission_action,
                resource=resolution.resource,
                context={**resolution.context, "environment": request.environment},
                risk_level=version.risk_level,
                resource_type="im_delivery_reconciliation",
                capability_version_id=version.id,
                agent_name=resolution.agent_name,
                run_id=request.run_id,
            ),
        )
        await self._policy_event(session, resolution, decision)
        if decision.effect != DecisionEffect.ALLOW or decision.obligations:
            await self._audit(
                session,
                resolution,
                version,
                decision,
                "DENIED",
                metadata={"operation": "reconcile", "reason": "delivery_not_authorized"},
            )
            return DingTalkRobotOutboxQueryResult(
                RobotQueryResult(RobotQueryState.UNKNOWN, reason="delivery_not_authorized"),
                decision.id,
                version.id,
                connector.id,
            )
        if not self._connector_grant_allows(connector, version):
            await self._audit(
                session,
                resolution,
                version,
                decision,
                "DENIED",
                metadata={"operation": "reconcile", "reason": "connector_grant_missing"},
            )
            return DingTalkRobotOutboxQueryResult(
                RobotQueryResult(RobotQueryState.UNKNOWN, reason="connector_grant_missing"),
                decision.id,
                version.id,
                connector.id,
            )
        try:
            self._validate(version.input_schema, resolution.payload, "capability_input_invalid")
            rate_allowed = await self.rate_limiter.allow(
                ":".join(
                    (
                        str(request.principal.organization_id),
                        str(request.principal.id),
                        str(version.id),
                        str(connector.id),
                    )
                ),
                _configured_rate_limit(connector),
            )
        except (RateLimitUnavailable, ObsionError):
            await self._audit(
                session,
                resolution,
                version,
                decision,
                "FAILED",
                metadata={"operation": "reconcile", "reason": "delivery_gateway_unavailable"},
            )
            return DingTalkRobotOutboxQueryResult(
                RobotQueryResult(RobotQueryState.UNKNOWN, reason="delivery_gateway_unavailable"),
                decision.id,
                version.id,
                connector.id,
            )
        if not rate_allowed:
            await self._audit(
                session,
                resolution,
                version,
                decision,
                "DENIED",
                metadata={"operation": "reconcile", "reason": "capability_rate_limited"},
            )
            return DingTalkRobotOutboxQueryResult(
                RobotQueryResult(RobotQueryState.UNKNOWN, reason="capability_rate_limited"),
                decision.id,
                version.id,
                connector.id,
            )

        started = perf_counter()
        credential: str | None = None
        try:
            credential = await self.credentials.resolve(
                connector.credential_ref,
                session=session,
                organization_id=request.principal.organization_id,
            )
            if not isinstance(credential, str) or not credential:
                raise ValidationError(
                    "credential_unavailable", "The connector credential is not available"
                )
            query = await (transport or DingTalkRobotTransport()).query_status(
                RobotCredentials(app_key=request.app_key, app_secret=credential),
                robot_code=request.robot_code,
                user_id=(
                    request.recipient_sender_id if request.conversation_type == "direct" else None
                ),
                open_conversation_id=(
                    request.recipient_conversation_id
                    if request.conversation_type == "group"
                    else None
                ),
                process_query_key=request.process_query_key,
            )
        except ObsionError:
            query = RobotQueryResult(RobotQueryState.UNKNOWN, reason="credential_unavailable")
        except (OSError, TimeoutError, ValueError):
            query = RobotQueryResult(RobotQueryState.UNKNOWN, reason="reconciliation_unavailable")
        finally:
            credential = None
        await self._audit(
            session,
            resolution,
            version,
            decision,
            "SUCCESS" if query.state == RobotQueryState.SUCCESS else query.state,
            latency_ms=int((perf_counter() - started) * 1000),
            metadata={
                "operation": "reconcile",
                "state": query.state,
                "send_status": query.send_status,
                "read_status": query.read_status,
                "reason": query.reason,
            },
        )
        return DingTalkRobotOutboxQueryResult(query, decision.id, version.id, connector.id)

    async def _invoke_operator_durable(
        self,
        session: AsyncSession,
        request: OperatorGatewayRequest,
    ) -> GatewayResult:
        invocation_id: UUID | None = None
        prepared: OperatorPreparedInvocation
        async with session.begin():
            prepared_or_result = await self._prepare_operator(session, request)
            if isinstance(prepared_or_result, GatewayResult):
                return prepared_or_result
            prepared = prepared_or_result
            if not prepared.idempotent_write:
                rate_result = await self._operator_rate_result(session, request, prepared)
                if rate_result is not None:
                    return rate_result
                return await self._execute_operator(session, request, prepared)

            fingerprint = operator_request_fingerprint(
                capability_name=request.capability_name,
                payload=request.payload,
                resource=request.resource,
                environment=request.environment,
                context=request.context,
            )
            try:
                claim = await self.operator_invocations.claim(
                    session,
                    request.principal,
                    request_id=request.correlation_id,
                    capability_name=request.capability_name,
                    capability_version_id=prepared.version.id,
                    connector_id=prepared.connector.id,
                    policy_decision_id=prepared.decision.id,
                    fingerprint=fingerprint,
                    lease_seconds=max(60, prepared.version.timeout_seconds + 30),
                    retention_hours=self.operator_idempotency_retention_hours,
                )
            except ConflictError as exc:
                await self._audit_operator(
                    session,
                    request,
                    prepared.version,
                    prepared.decision,
                    "DENIED",
                    metadata={"error_code": exc.code, "idempotency": "CONFLICT"},
                )
                return GatewayResult(
                    status=GatewayStatus.DENIED,
                    policy_decision_id=prepared.decision.id,
                    error_code=exc.code,
                    error_message=exc.message,
                    capability_version_id=prepared.version.id,
                    connector_id=prepared.connector.id,
                )
            if claim.state == "REPLAY":
                assert claim.replayed_result is not None
                await self._audit_operator(
                    session,
                    request,
                    prepared.version,
                    prepared.decision,
                    "REPLAYED",
                    metadata={
                        "idempotency": "REPLAY",
                        "operator_invocation_id": str(claim.record.id),
                    },
                )
                return self._operator_result_from_record(
                    claim.replayed_result,
                    record=claim.record,
                    policy_decision_id=prepared.decision.id,
                )
            if claim.state in {"IN_PROGRESS", "UNKNOWN"}:
                unknown = claim.state == "UNKNOWN"
                code = (
                    "operator_invocation_outcome_unknown"
                    if unknown
                    else "idempotency_request_in_progress"
                )
                message = (
                    "The previous operator Capability outcome requires reconciliation"
                    if unknown
                    else "The original operator Capability request is still in progress"
                )
                await self._audit_operator(
                    session,
                    request,
                    prepared.version,
                    prepared.decision,
                    "UNKNOWN" if unknown else "IN_PROGRESS",
                    metadata={
                        "error_code": code,
                        "idempotency": claim.state,
                        "operator_invocation_id": str(claim.record.id),
                    },
                )
                return GatewayResult(
                    status=GatewayStatus.FAILED,
                    policy_decision_id=prepared.decision.id,
                    error_code=code,
                    error_message=message,
                    capability_version_id=claim.record.capability_version_id,
                    connector_id=claim.record.connector_id,
                )
            invocation_id = claim.record.id
            rate_result = await self._operator_rate_result(session, request, prepared)
            if rate_result is not None:
                await self.operator_invocations.complete(
                    session,
                    invocation_id,
                    result=self._operator_result_record(rate_result),
                    succeeded=False,
                )
                rate_invocation = await session.scalar(
                    select(OperatorCapabilityInvocation)
                    .where(OperatorCapabilityInvocation.id == invocation_id)
                    .with_for_update()
                )
                assert rate_invocation is not None
                rate_invocation.error_code = rate_result.error_code
                rate_invocation.error_message = rate_result.error_message
                await session.flush()
                return rate_result

        try:
            async with session.begin():
                result = await self._execute_operator(session, request, prepared)
                await self.operator_invocations.complete(
                    session,
                    invocation_id,
                    result=self._operator_result_record(result),
                    succeeded=result.status == GatewayStatus.COMPLETED,
                )
                completed_invocation = await session.scalar(
                    select(OperatorCapabilityInvocation)
                    .where(OperatorCapabilityInvocation.id == invocation_id)
                    .with_for_update()
                )
                assert completed_invocation is not None
                completed_invocation.error_code = result.error_code
                completed_invocation.error_message = result.error_message
                await session.flush()
                return result
        except BaseException:
            if session.in_transaction():
                await session.rollback()
            async with session.begin():
                await self.operator_invocations.mark_unknown(session, invocation_id)
                await self._audit_operator(
                    session,
                    request,
                    prepared.version,
                    prepared.decision,
                    "UNKNOWN",
                    metadata={
                        "error_code": "operator_invocation_outcome_unknown",
                        "operator_invocation_id": str(invocation_id),
                    },
                )
            raise

    async def _prepare_operator(
        self,
        session: AsyncSession,
        request: OperatorGatewayRequest,
    ) -> OperatorPreparedInvocation | GatewayResult:
        _, version, connector = await self._resolve(session, request)
        is_source_write = (
            request.capability_name in {"knowledge.ingest", "knowledge.sync"}
            and version.permission_action == "knowledge.write"
            and version.risk_level == RiskLevel.L2
            and version.side_effect == SideEffect.IDEMPOTENT_WRITE
        )
        is_source_browse = (
            request.capability_name in VENDOR_KNOWLEDGE_BROWSE_OPERATIONS
            and version.permission_action == "knowledge.write"
            and version.risk_level == RiskLevel.L1
            and version.side_effect == SideEffect.NONE
        )
        is_codeup_read = (
            request.capability_name in CODEUP_ALL_OPERATIONS
            and version.permission_action
            == (
                "connectors.read"
                if request.capability_name == CODEUP_DISCOVERY_OPERATION
                else "code.read"
            )
            and version.risk_level == RiskLevel.L2
            and version.side_effect == SideEffect.NONE
            and (is_codeup_connector(connector) or is_codeup_catalog_connector(connector))
        )
        is_source_operation = is_source_write or is_source_browse or is_codeup_read
        decision = await self.policy.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=request.principal,
                action=version.permission_action,
                resource=request.resource,
                context={
                    **request.context,
                    "environment": request.environment,
                    "invocation_mode": "operator",
                },
                risk_level=version.risk_level if is_source_operation else RiskLevel.L3,
                resource_type="capability",
                capability_version_id=version.id,
                run_id=None,
            ),
        )
        if decision.effect in {DecisionEffect.DENY, DecisionEffect.ASK}:
            reason = (
                "operator_capability_approval_requires_run"
                if decision.effect == DecisionEffect.ASK
                else "operator_capability_denied"
            )
            await self._audit_operator(
                session,
                request,
                version,
                decision,
                "DENIED",
                metadata={"reason": reason, "reasons": decision.reason_codes},
            )
            return GatewayResult(
                status=GatewayStatus.DENIED,
                policy_decision_id=decision.id,
                error_code="capability_denied",
                error_message=(
                    "Operator Capability approval requires a durable Harness Run"
                    if decision.effect == DecisionEffect.ASK
                    else "The operator Capability request was denied by policy"
                ),
                capability_version_id=version.id,
                connector_id=connector.id,
            )
        if await self._codeup_repository_denied(session, connector, version, request):
            await self._audit_operator(
                session,
                request,
                version,
                decision,
                "DENIED",
                metadata={"error_code": "codeup_repository_denied"},
            )
            return GatewayResult(
                status=GatewayStatus.DENIED,
                policy_decision_id=decision.id,
                error_code="codeup_repository_denied",
                error_message="云效仓库未获授权，请检查项目权限与仓库映射。",
                capability_version_id=version.id,
                connector_id=connector.id,
            )
        if not self._connector_grant_allows(connector, version):
            await self._audit_operator(
                session,
                request,
                version,
                decision,
                "DENIED",
                metadata={"error_code": "connector_grant_missing"},
            )
            return GatewayResult(
                status=GatewayStatus.DENIED,
                policy_decision_id=decision.id,
                error_code="connector_grant_missing",
                error_message="The connector is not granted this capability",
                capability_version_id=version.id,
                connector_id=connector.id,
            )
        try:
            self._validate(version.input_schema, request.payload, "capability_input_invalid")
        except ValidationError as exc:
            await self._audit_operator(
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

        executor = self.executors.get(version.transport.value)
        if executor is None:
            await self._audit_operator(session, request, version, decision, "FAILED")
            return GatewayResult(
                status=GatewayStatus.FAILED,
                policy_decision_id=decision.id,
                error_code="capability_transport_unavailable",
                error_message="No executor is installed for the capability transport",
                capability_version_id=version.id,
                connector_id=connector.id,
            )
        return OperatorPreparedInvocation(
            version=version,
            connector=connector,
            decision=decision,
            executor=executor,
            idempotent_write=is_source_write,
        )

    async def _operator_rate_result(
        self,
        session: AsyncSession,
        request: OperatorGatewayRequest,
        prepared: OperatorPreparedInvocation,
    ) -> GatewayResult | None:
        version = prepared.version
        connector = prepared.connector
        decision = prepared.decision
        configured_limit = connector.configuration.get("rate_limit_per_minute")
        limit = (
            configured_limit
            if isinstance(configured_limit, int) and not isinstance(configured_limit, bool)
            else None
        )
        rate_key = ":".join(
            (
                str(request.principal.organization_id),
                str(request.principal.id),
                str(version.id),
                str(connector.id),
            )
        )
        try:
            rate_allowed = await self.rate_limiter.allow(rate_key, limit)
        except RateLimitUnavailable:
            await self._audit_operator(
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
        if rate_allowed:
            return None
        await self._audit_operator(
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

    async def _execute_operator(
        self,
        session: AsyncSession,
        request: OperatorGatewayRequest,
        prepared: OperatorPreparedInvocation,
    ) -> GatewayResult:
        version = prepared.version
        connector = prepared.connector
        decision = prepared.decision
        executor = prepared.executor
        started = perf_counter()
        credential: str | None = None
        try:
            credential = await self.credentials.resolve(
                connector.credential_ref,
                session=session,
                organization_id=request.principal.organization_id,
            )
            async with session.begin_nested():
                connector_result = await asyncio.wait_for(
                    executor.invoke(
                        connector,
                        request.payload,
                        credential,
                        ConnectorContext(
                            principal=request.principal,
                            run_id=None,
                            step_id=None,
                            correlation_id=request.correlation_id,
                            session=session,
                            credential=credential,
                        ),
                    ),
                    timeout=version.timeout_seconds,
                )
                if await self._codeup_repository_denied(session, connector, version, request):
                    raise AuthorizationError(
                        "codeup_repository_denied", "读取期间仓库授权已失效，结果未予交付。"
                    )
                self._validate(
                    version.output_schema,
                    connector_result.data,
                    "capability_output_invalid",
                )
                output = apply_obligations(connector_result.data, decision.obligations)
            latency_ms = int((perf_counter() - started) * 1000)
            await self._audit_operator(
                session,
                request,
                version,
                decision,
                "SUCCESS",
                latency_ms=latency_ms,
                metadata={
                    "connector_id": str(connector.id),
                    "source": connector_result.source,
                    "result_resource": connector_result.resource,
                },
            )
            return GatewayResult(
                status=GatewayStatus.COMPLETED,
                policy_decision_id=decision.id,
                output=output,
                capability_version_id=version.id,
                connector_id=connector.id,
            )
        except Exception as exc:
            latency_ms = int((perf_counter() - started) * 1000)
            if isinstance(exc, TimeoutError):
                error_code = "capability_timeout"
                error_message = "The connector exceeded the capability timeout"
            elif isinstance(exc, ObsionError):
                error_code = exc.code
                error_message = exc.message
            else:
                error_code = "capability_failed"
                error_message = "The connector could not complete the request"
            await self._audit_operator(
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
                error_message=error_message,
                capability_version_id=version.id,
                connector_id=connector.id,
            )
        finally:
            credential = None

    @staticmethod
    def _operator_result_record(result: GatewayResult) -> dict[str, Any]:
        return {
            "status": result.status.value,
            "output": result.output,
            "error_code": result.error_code,
            "error_message": result.error_message,
            "capability_version_id": (
                str(result.capability_version_id) if result.capability_version_id else None
            ),
            "connector_id": str(result.connector_id) if result.connector_id else None,
        }

    @staticmethod
    def _operator_result_from_record(
        stored: dict[str, Any],
        *,
        record: OperatorCapabilityInvocation,
        policy_decision_id: UUID,
    ) -> GatewayResult:
        capability_version_id = stored.get("capability_version_id")
        connector_id = stored.get("connector_id")
        return GatewayResult(
            status=GatewayStatus(str(stored["status"])),
            policy_decision_id=policy_decision_id,
            output=stored.get("output") if isinstance(stored.get("output"), dict) else None,
            error_code=record.error_code,
            error_message=record.error_message,
            capability_version_id=(
                UUID(str(capability_version_id)) if capability_version_id else None
            ),
            connector_id=UUID(str(connector_id)) if connector_id else None,
        )

    async def _invoke(self, session: AsyncSession, request: GatewayRequest) -> GatewayResult:
        definition, version, connector = await self._resolve(session, request)
        agent_capability_allowed = await self._agent_capability_allowed(
            session, request, definition.name, version
        )
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
                context={**request.context, "environment": request.environment},
                agent_name=request.agent_name,
                agent_version_id=request.agent_version_id,
                agent_capability_allowed=agent_capability_allowed,
                run_id=request.run_id,
            ),
        )
        await self._policy_event(session, request, decision)
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
        repository_denied = self._engineering_repository_denied(connector, request)
        repository_denied = repository_denied or await self._codeup_repository_denied(
            session, connector, version, request
        )
        if not self._connector_grant_allows(connector, version) or repository_denied:
            error_code = (
                "engineering_repository_denied" if repository_denied else "connector_grant_missing"
            )
            await self._audit(
                session,
                request,
                version,
                decision,
                "DENIED",
                metadata={"error_code": error_code},
            )
            return GatewayResult(
                status=GatewayStatus.DENIED,
                policy_decision_id=decision.id,
                error_code=error_code,
                error_message=(
                    "The repository is not allowed by the engineering connector"
                    if repository_denied
                    else "The connector is not granted this capability"
                ),
                capability_version_id=version.id,
                connector_id=connector.id,
            )
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
        credential: str | None = None
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
                        session=session,
                        credential=credential,
                    ),
                ),
                timeout=version.timeout_seconds,
            )
            if await self._codeup_repository_denied(session, connector, version, request):
                raise AuthorizationError(
                    "codeup_repository_denied", "读取期间仓库授权已失效，结果未予交付。"
                )
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
            event_error_code = (
                "capability_timeout" if isinstance(exc, TimeoutError) else "capability_failed"
            )
            if isinstance(exc, TimeoutError):
                error_code = "capability_timeout"
                error_message = "The connector exceeded the capability timeout"
            elif isinstance(exc, ObsionError):
                error_code = exc.code
                error_message = exc.message
            else:
                error_code = "capability_failed"
                error_message = "The connector could not complete the request"
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
                    payload={"capability": definition.name, "error_code": event_error_code},
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
                error_message=error_message,
                capability_version_id=version.id,
                connector_id=connector.id,
            )
        finally:
            credential = None

    async def _resolve(
        self,
        session: AsyncSession,
        request: GatewayRequest | OperatorGatewayRequest,
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
                CapabilityVersion.organization_id == request.principal.organization_id,
                CapabilityBinding.organization_id == request.principal.organization_id,
                CapabilityBinding.environment == request.environment,
                CapabilityBinding.enabled.is_(True),
                Connector.organization_id == request.principal.organization_id,
                Connector.environment == request.environment,
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
    async def _agent_capability_allowed(
        session: AsyncSession,
        request: GatewayRequest,
        capability_name: str,
        version: CapabilityVersion,
    ) -> bool | None:
        """Re-check the pinned AgentSpec at the execution boundary.

        Planner filtering is necessary but not sufficient: a persisted plan or a
        direct caller must not turn a capability outside the pinned AgentSpec's
        capability and risk budget into an executable request.
        """
        if request.agent_version_id is None:
            return None
        agent_version = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.id == request.agent_version_id,
                AgentVersion.organization_id == request.principal.organization_id,
            )
        )
        if agent_version is None or not isinstance(agent_version.spec, dict):
            return False
        sandbox = agent_version.spec.get("sandbox")
        if sandbox is not None:
            if not isinstance(sandbox, dict):
                return False
            network = sandbox.get("network")
            if network not in (None, "deny", "gateway-only"):
                return False
            if not sandbox_allows_capabilities(sandbox):
                return False
        capabilities = agent_version.spec.get("capabilities")
        risk_policy = agent_version.spec.get("riskPolicy")
        max_level = risk_policy.get("maxLevel") if isinstance(risk_policy, dict) else None
        if not isinstance(capabilities, list) or capability_name not in capabilities:
            return False
        if not isinstance(max_level, str) or not max_level.startswith("L"):
            return False
        try:
            return version.risk_level.ordinal <= int(max_level[1:])
        except ValueError:
            return False

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
    def _connector_grant_allows(connector: Connector, version: CapabilityVersion) -> bool:
        grants = connector.declared_grants
        return "*" in grants or version.permission_action in grants

    @staticmethod
    def _dingtalk_robot_contract_valid(
        definition: CapabilityDefinition,
        version: CapabilityVersion,
        connector: Connector,
        request: DingTalkRobotOutboxRequest | DingTalkRobotOutboxQueryRequest,
    ) -> bool:
        return (
            definition.name == DINGTALK_ROBOT_CAPABILITY
            and version.risk_level == RiskLevel.L2
            and version.side_effect == SideEffect.WRITE
            and version.permission_action == "im.reply.deliver"
            and version.transport.value == "HTTP"
            and connector.id == request.connector_id
            and connector.connector_type == "dingtalk-robot"
            and connector.configuration.get("protocol") == DINGTALK_ROBOT_PROTOCOL
            and request.conversation_type in {"direct", "group"}
            and (
                request.conversation_type == "direct"
                or isinstance(request.recipient_conversation_id, str)
                and bool(request.recipient_conversation_id.strip())
            )
            and request.connector_fingerprint
            == connector_fingerprint(
                connector_type=connector.connector_type,
                environment=connector.environment,
                endpoint=connector.endpoint,
                configuration=connector.configuration,
                credential_ref=connector.credential_ref,
                declared_grants=connector.declared_grants,
                allowed_egress=connector.allowed_egress,
            )
        )

    @staticmethod
    async def _codeup_repository_denied(
        session: AsyncSession,
        connector: Connector,
        version: CapabilityVersion,
        request: GatewayRequest | OperatorGatewayRequest,
    ) -> bool:
        if (
            not is_codeup_connector(connector)
            and not is_codeup_catalog_connector(connector)
            and request.capability_name not in CODEUP_ALL_OPERATIONS
        ):
            return False
        if request.capability_name == CODEUP_DISCOVERY_OPERATION:
            return not (
                isinstance(request, OperatorGatewayRequest)
                and is_codeup_catalog_connector(connector)
                and request.payload.get("operation") == CODEUP_DISCOVERY_OPERATION
                and version.permission_action == "connectors.read"
                and version.side_effect == SideEffect.NONE
                and version.risk_level == RiskLevel.L2
                and version.data_classification == Classification.RESTRICTED
                and await catalog_allowed(
                    session, request.principal, connector, request.payload, request.resource
                )
            )
        if (
            not is_codeup_connector(connector)
            or request.capability_name not in CODEUP_OPERATIONS
            or request.payload.get("operation") != request.capability_name
            or version.permission_action != "code.read"
            or version.side_effect != SideEffect.NONE
            or version.risk_level != RiskLevel.L2
            or version.data_classification != Classification.RESTRICTED
        ):
            return True
        return not await repository_allowed(
            session, request.principal, connector, request.payload, request.resource
        )

    @staticmethod
    def _engineering_repository_denied(
        connector: Connector,
        request: GatewayRequest | OperatorGatewayRequest,
    ) -> bool:
        allowed = connector.configuration.get("allowed_repositories")
        if not isinstance(allowed, list) or not allowed:
            return False
        repository = request.resource.get("repository")
        if not isinstance(repository, str):
            repository = request.payload.get("repository")
        return not isinstance(repository, str) or repository not in allowed

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
        mapping_type = version.evidence_mapping.get("type", "TOOL")
        try:
            evidence_type = EvidenceType(mapping_type)
        except ValueError:
            evidence_type = EvidenceType.TOOL
        evidence = await self.evidence.persist(
            session,
            EvidenceInput(
                organization_id=request.principal.organization_id,
                run_id=request.run_id,
                step_id=request.step_id,
                evidence_type=evidence_type,
                source=source,
                resource=resource,
                observed_at=observed_at or utc_now(),
                content=output,
                confidence=version.evidence_mapping.get("confidence", 1.0),
                classification=version.data_classification,
                permissions=(version.permission_action,),
                lineage={
                    "capability_version_id": str(version.id),
                    "connector_id": str(connector.id),
                    "policy_decision_id": str(policy_decision_id),
                    "request_resource": redact(request.resource),
                },
            ),
        )
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
                agent_version_id=request.agent_version_id,
                model_profile_id=request.model_profile_id,
                capability_version_id=version.id,
                resource=request.resource,
                result_classification=version.data_classification,
            ),
        )

    async def _audit_operator(
        self,
        session: AsyncSession,
        request: OperatorGatewayRequest,
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
                correlation_id=request.correlation_id,
                actor_type=ActorType.USER,
                actor_id=request.principal.id,
                action=version.permission_action,
                resource_type="capability",
                resource_id=str(version.id),
                outcome=outcome,
                risk_level=version.risk_level,
                policy_decision_id=decision.id,
                metadata={
                    "invocation_mode": "operator",
                    "capability": request.capability_name,
                    **(metadata or {}),
                },
                latency_ms=latency_ms,
                capability_version_id=version.id,
                resource=request.resource,
                result_classification=version.data_classification,
            ),
        )
