import fnmatch
import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.db.models import CapabilityVersion, Policy, PolicyDecision
from obsion.domain.enums import DecisionEffect, RiskLevel, SideEffect
from obsion.security.identity import Principal
from obsion.security.redaction import redact
from obsion.telemetry import policy_counter, tracer

_EFFECT_STRENGTH = {
    DecisionEffect.ALLOW: 0,
    DecisionEffect.MASK: 1,
    DecisionEffect.ASK: 2,
    DecisionEffect.DENY: 3,
}


@dataclass(frozen=True, slots=True)
class PolicyInput:
    principal: Principal
    capability: CapabilityVersion
    action: str
    resource: dict[str, Any]
    context: dict[str, Any]
    agent_name: str
    agent_version_id: UUID | None = None
    agent_capability_allowed: bool | None = None
    run_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ActionPolicyInput:
    principal: Principal
    capability: CapabilityVersion
    action: str
    resource: dict[str, Any]
    context: dict[str, Any]
    action_request_id: UUID
    approval_valid: bool


@dataclass(frozen=True, slots=True)
class ResourcePolicyInput:
    """Policy input for governed control-plane resources without a Capability."""

    principal: Principal
    action: str
    resource: dict[str, Any]
    context: dict[str, Any]
    risk_level: RiskLevel
    resource_type: str
    agent_name: str = "control-plane"
    agent_version_id: UUID | None = None
    run_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    id: UUID
    effect: DecisionEffect
    obligations: tuple[dict[str, Any], ...] = ()
    reason_codes: tuple[str, ...] = ()
    matched_policy_ids: tuple[UUID, ...] = ()


def _matches_scalar(actual: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return actual in expected
    if isinstance(expected, str) and "*" in expected:
        return fnmatch.fnmatch(str(actual), expected)
    return bool(actual == expected)


def _string_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {str(item) for item in value}
    return set()


def _patterns(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return ()


def _principal_fingerprint(principal: Principal) -> dict[str, Any]:
    return {
        "principal": str(principal.id),
        "department_id": str(principal.department_id) if principal.department_id else None,
        "department": principal.department,
        "roles": sorted(principal.roles),
        "permissions": sorted(principal.permissions),
        "attributes": redact(principal.attributes),
    }


def _matches_mapping(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, value in expected.items():
        current: Any = actual
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        if not _matches_scalar(current, value):
            return False
    return True


def _policy_matches(
    policy: Policy,
    *,
    principal: Principal,
    action: str,
    resource: dict[str, Any],
    context: dict[str, Any],
    agent_name: str,
    agent_version_id: UUID | None,
    risk_level: RiskLevel,
) -> bool:
    conditions = policy.conditions if isinstance(policy.conditions, dict) else {}
    actions = _patterns(conditions.get("actions", ["*"]))
    if not any(fnmatch.fnmatch(action, pattern) for pattern in actions):
        return False
    roles_any = _string_set(conditions.get("roles_any", []))
    if roles_any and not roles_any.intersection(principal.roles):
        return False
    roles_all = _string_set(conditions.get("roles_all", []))
    if roles_all and not roles_all.issubset(principal.roles):
        return False
    permissions_any = _string_set(conditions.get("permissions_any", []))
    if (
        permissions_any
        and "*" not in principal.permissions
        and not permissions_any.intersection(principal.permissions)
    ):
        return False
    permissions_all = _string_set(conditions.get("permissions_all", []))
    if (
        permissions_all
        and "*" not in principal.permissions
        and not permissions_all.issubset(principal.permissions)
    ):
        return False
    user_ids = _string_set(
        conditions.get("user_ids", conditions.get("users_any", conditions.get("users", [])))
    )
    if user_ids and str(principal.id) not in user_ids:
        return False
    department_ids = _string_set(
        conditions.get(
            "department_ids",
            conditions.get("departments_any", conditions.get("departments", [])),
        )
    )
    if department_ids and (
        principal.department_id is None or str(principal.department_id) not in department_ids
    ):
        return False
    department_names = _string_set(conditions.get("department_names", []))
    if department_names and principal.department not in department_names:
        return False
    attributes = conditions.get("attributes", {})
    if attributes and (
        not isinstance(attributes, dict) or not _matches_mapping(principal.attributes, attributes)
    ):
        return False
    agents = _patterns(conditions.get("agents", ["*"]))
    if not any(fnmatch.fnmatch(agent_name, pattern) for pattern in agents):
        return False
    agent_versions = _string_set(conditions.get("agent_version_ids", []))
    if agent_versions and (agent_version_id is None or str(agent_version_id) not in agent_versions):
        return False
    max_risk = conditions.get("max_risk")
    if max_risk:
        try:
            if risk_level.ordinal > RiskLevel(max_risk).ordinal:
                return False
        except ValueError:
            return False
    resource_conditions = conditions.get("resource", {})
    context_conditions = conditions.get("context", {})
    if not isinstance(resource_conditions, dict) or not isinstance(context_conditions, dict):
        return False
    if not _matches_mapping(resource, resource_conditions):
        return False
    return _matches_mapping(context, context_conditions)


class PolicyEngine:
    async def evaluate(self, session: AsyncSession, request: PolicyInput) -> Decision:
        with tracer.start_as_current_span("obsion.policy.evaluate") as span:
            effect, obligations, reasons, policy_ids = await self._resolve(session, request)
            span.set_attribute("obsion.policy.action", request.action)
            span.set_attribute("obsion.policy.effect", effect.value)
            span.set_attribute("obsion.policy.risk", request.capability.risk_level.value)
            policy_counter.add(
                1,
                {
                    "action": request.action,
                    "effect": effect.value,
                    "risk": request.capability.risk_level.value,
                },
            )
        safe_input = {
            **_principal_fingerprint(request.principal),
            "agent": request.agent_name,
            "agent_version_id": str(request.agent_version_id) if request.agent_version_id else None,
            "agent_capability_allowed": request.agent_capability_allowed,
            "capability": str(request.capability.id),
            "action": request.action,
            "resource": redact(request.resource),
            "context": redact(request.context),
            "risk": request.capability.risk_level,
            "effect": effect,
            "matched_policy_ids": [str(policy_id) for policy_id in policy_ids],
        }
        fingerprint = hashlib.sha256(
            json.dumps(safe_input, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        model = PolicyDecision(
            id=new_id(),
            organization_id=request.principal.organization_id,
            run_id=request.run_id,
            principal_id=request.principal.id,
            agent_version_id=request.agent_version_id,
            capability_version_id=request.capability.id,
            action=request.action,
            resource=redact(request.resource),
            context=redact(request.context),
            risk_level=request.capability.risk_level,
            effect=effect,
            matched_policy_ids=[str(policy_id) for policy_id in policy_ids],
            obligations=list(obligations),
            reason_codes=list(reasons),
            input_fingerprint=fingerprint,
            created_at=utc_now(),
        )
        session.add(model)
        await session.flush()
        return Decision(
            id=model.id,
            effect=effect,
            obligations=tuple(obligations),
            reason_codes=tuple(reasons),
            matched_policy_ids=tuple(policy_ids),
        )

    async def evaluate_resource(
        self,
        session: AsyncSession,
        request: ResourcePolicyInput,
    ) -> Decision:
        """Evaluate and persist a policy decision for a control-plane resource.

        Memory, artifact, and registry operations still require durable policy
        evidence even though they do not invoke an external Capability. L3-L5
        resource mutations remain denied by default unless a dedicated workflow
        supplies a narrower policy surface.
        """
        policies = list(
            await session.scalars(
                select(Policy)
                .where(
                    Policy.organization_id == request.principal.organization_id,
                    Policy.enabled.is_(True),
                )
                .order_by(Policy.priority.desc(), Policy.created_at.desc())
            )
        )
        matching = (
            []
            if request.risk_level.ordinal >= RiskLevel.L3.ordinal
            else [
                policy
                for policy in policies
                if _policy_matches(
                    policy,
                    principal=request.principal,
                    action=request.action,
                    resource=request.resource,
                    context=request.context,
                    agent_name=request.agent_name,
                    agent_version_id=request.agent_version_id,
                    risk_level=request.risk_level,
                )
            ]
        )
        obligations: list[dict[str, Any]] = []
        policy_ids: list[UUID] = []
        if request.risk_level.ordinal >= RiskLevel.L3.ordinal:
            effect = DecisionEffect.DENY
            reasons = ["high_risk_resource_mutation_denied"]
        elif matching:
            strongest = max(matching, key=lambda policy: _EFFECT_STRENGTH[policy.effect])
            selected = [policy for policy in matching if policy.effect == strongest.effect]
            if strongest.effect != DecisionEffect.DENY and not request.principal.can(
                request.action
            ):
                effect = DecisionEffect.DENY
                reasons = ["no_matching_grant"]
            else:
                effect = strongest.effect
                obligations = [item for policy in selected for item in policy.obligations]
                reasons = [f"policy:{policy.name}:v{policy.version}" for policy in selected]
                policy_ids = [policy.id for policy in selected]
        elif not request.principal.can(request.action):
            effect = DecisionEffect.DENY
            reasons = ["no_matching_grant"]
        elif request.risk_level == RiskLevel.L2:
            effect = DecisionEffect.MASK
            obligations = [{"type": "redact_sensitive_fields"}]
            reasons = ["default_sensitive_resource"]
            policy_ids = []
        else:
            effect = DecisionEffect.ALLOW
            obligations = []
            reasons = ["principal_permission"]
            policy_ids = []

        safe_input = {
            **_principal_fingerprint(request.principal),
            "agent": request.agent_name,
            "agent_version_id": str(request.agent_version_id) if request.agent_version_id else None,
            "resource_type": request.resource_type,
            "action": request.action,
            "resource": redact(request.resource),
            "context": redact(request.context),
            "risk": request.risk_level,
            "effect": effect,
            "matched_policy_ids": [str(policy_id) for policy_id in policy_ids],
        }
        fingerprint = hashlib.sha256(
            json.dumps(safe_input, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        model = PolicyDecision(
            id=new_id(),
            organization_id=request.principal.organization_id,
            run_id=request.run_id,
            principal_id=request.principal.id,
            agent_version_id=None,
            capability_version_id=None,
            action=request.action,
            resource=redact(request.resource),
            context=redact(request.context),
            risk_level=request.risk_level,
            effect=effect,
            matched_policy_ids=[str(policy_id) for policy_id in policy_ids],
            obligations=list(obligations),
            reason_codes=list(reasons),
            input_fingerprint=fingerprint,
            created_at=utc_now(),
        )
        session.add(model)
        await session.flush()
        policy_counter.add(
            1,
            {
                "action": request.action,
                "effect": effect.value,
                "risk": request.risk_level.value,
            },
        )
        return Decision(
            id=model.id,
            effect=effect,
            obligations=tuple(obligations),
            reason_codes=tuple(reasons),
            matched_policy_ids=tuple(policy_ids),
        )

    async def evaluate_action(self, session: AsyncSession, request: ActionPolicyInput) -> Decision:
        """Evaluate the closed Phase 7 write boundary.

        This entry point is called only by ActionGateway. Generic capability
        invocation continues to use ``evaluate`` and remains read-only.
        """
        policy_input = PolicyInput(
            principal=request.principal,
            capability=request.capability,
            action=request.action,
            resource=request.resource,
            context=request.context,
            agent_name="action-agent",
            run_id=None,
        )
        effect = DecisionEffect.ALLOW
        reasons: list[str] = ["governed_action_approved"]
        policy_ids: list[UUID] = []
        environment = str(request.context.get("environment", ""))
        if environment not in {"development", "staging"}:
            effect = DecisionEffect.DENY
            reasons = ["v1_production_action_boundary"]
        elif (
            request.capability.risk_level != RiskLevel.L3
            or request.capability.side_effect != SideEffect.IDEMPOTENT_WRITE
        ):
            effect = DecisionEffect.DENY
            reasons = ["v1_action_capability_boundary"]
        elif not request.approval_valid:
            effect = DecisionEffect.DENY
            reasons = ["action_approval_invalid"]
        elif not request.principal.can("action.execute") or not request.principal.can(
            request.action
        ):
            effect = DecisionEffect.DENY
            reasons = ["no_matching_grant"]
        else:
            policies = list(
                await session.scalars(
                    select(Policy)
                    .where(
                        Policy.organization_id == request.principal.organization_id,
                        Policy.enabled.is_(True),
                    )
                    .order_by(Policy.priority.desc(), Policy.created_at.desc())
                )
            )
            matching = [
                policy
                for policy in policies
                if _policy_matches(
                    policy,
                    principal=policy_input.principal,
                    action=policy_input.action,
                    resource=policy_input.resource,
                    context=policy_input.context,
                    agent_name=policy_input.agent_name,
                    agent_version_id=policy_input.agent_version_id,
                    risk_level=policy_input.capability.risk_level,
                )
            ]
            denying = [policy for policy in matching if policy.effect == DecisionEffect.DENY]
            if denying:
                effect = DecisionEffect.DENY
                reasons = [f"policy:{policy.name}:v{policy.version}" for policy in denying]
                policy_ids = [policy.id for policy in denying]
            elif matching:
                reasons.extend(f"policy:{policy.name}:v{policy.version}" for policy in matching)
                policy_ids = [policy.id for policy in matching]

        safe_input = {
            **_principal_fingerprint(request.principal),
            "action_request_id": str(request.action_request_id),
            "capability": str(request.capability.id),
            "action": request.action,
            "resource": redact(request.resource),
            "context": redact(request.context),
            "risk": request.capability.risk_level,
            "approval_valid": request.approval_valid,
        }
        fingerprint = hashlib.sha256(
            json.dumps(safe_input, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        model = PolicyDecision(
            id=new_id(),
            organization_id=request.principal.organization_id,
            run_id=None,
            principal_id=request.principal.id,
            agent_version_id=None,
            capability_version_id=request.capability.id,
            action=request.action,
            resource=redact(request.resource),
            context=redact(request.context),
            risk_level=request.capability.risk_level,
            effect=effect,
            matched_policy_ids=[str(policy_id) for policy_id in policy_ids],
            obligations=[],
            reason_codes=reasons,
            input_fingerprint=fingerprint,
            created_at=utc_now(),
        )
        session.add(model)
        await session.flush()
        policy_counter.add(
            1,
            {
                "action": request.action,
                "effect": effect.value,
                "risk": request.capability.risk_level.value,
            },
        )
        return Decision(
            id=model.id,
            effect=effect,
            reason_codes=tuple(reasons),
            matched_policy_ids=tuple(policy_ids),
        )

    async def _resolve(
        self, session: AsyncSession, request: PolicyInput
    ) -> tuple[DecisionEffect, list[dict[str, Any]], list[str], list[UUID]]:
        capability = request.capability
        if request.agent_capability_allowed is False:
            return DecisionEffect.DENY, [], ["agent_capability_not_allowed"], []
        if capability.side_effect != SideEffect.NONE or capability.risk_level.ordinal >= 3:
            return DecisionEffect.DENY, [], ["v1_read_only_boundary"], []

        policies = list(
            await session.scalars(
                select(Policy)
                .where(
                    Policy.organization_id == request.principal.organization_id,
                    Policy.enabled.is_(True),
                )
                .order_by(Policy.priority.desc(), Policy.created_at.desc())
            )
        )
        matching = [
            policy
            for policy in policies
            if _policy_matches(
                policy,
                principal=request.principal,
                action=request.action,
                resource=request.resource,
                context=request.context,
                agent_name=request.agent_name,
                agent_version_id=request.agent_version_id,
                risk_level=request.capability.risk_level,
            )
        ]
        if matching:
            strongest = max(matching, key=lambda policy: _EFFECT_STRENGTH[policy.effect])
            same_effect = [policy for policy in matching if policy.effect == strongest.effect]
            if strongest.effect != DecisionEffect.DENY and not request.principal.can(
                request.action
            ):
                return DecisionEffect.DENY, [], ["no_matching_grant"], []
            obligations = [item for policy in same_effect for item in policy.obligations]
            return (
                strongest.effect,
                obligations,
                [f"policy:{policy.name}:v{policy.version}" for policy in same_effect],
                [policy.id for policy in same_effect],
            )

        has_grant = request.principal.can(request.action)
        if not has_grant:
            return DecisionEffect.DENY, [], ["no_matching_grant"], []
        if capability.risk_level == RiskLevel.L2:
            obligations = [
                {"type": "mask_classified_fields"},
                {"type": "limit_result_rows", "value": 500},
            ]
            return DecisionEffect.MASK, obligations, ["default_sensitive_read"], []
        return DecisionEffect.ALLOW, [], ["principal_permission"], []
