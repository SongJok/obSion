"""Resolve typed task context against current source access before planning."""

from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import NotFoundError
from obsion.db.models import Turn
from obsion.domain.enums import DecisionEffect, RiskLevel
from obsion.domain.run_intent import RunIntent, make_resolved_slot
from obsion.domain.task_context import TASK_CONTEXT_SLOTS, requested_format, task_context_reference
from obsion.knowledge.service import KnowledgeService
from obsion.security.identity import Principal
from obsion.security.policy import PolicyEngine, ResourcePolicyInput


async def resolve_task_context(
    session: AsyncSession,
    principal: Principal,
    turn: Turn,
    intent: RunIntent,
    previous: tuple[RunIntent, str] | None,
) -> RunIntent:
    reference = task_context_reference(turn.context_refs)
    slots = {item.slot: item for item in intent.resolved_slots}
    if previous:
        for item in previous[0].resolved_slots:
            if item.slot in TASK_CONTEXT_SLOTS and item.slot not in slots:
                slots[item.slot] = make_resolved_slot(
                    slot=item.slot,
                    value=item.value,
                    source="CONVERSATION",
                    source_ref=previous[1],
                    confidence_rank=89,
                )
    explicit: dict[str, JsonValue] = {}
    if any(item.get("type") == "repository" for item in turn.context_refs):
        explicit["selected_documents"] = []
    if reference:
        if "constraints" in reference.model_fields_set:
            explicit["task_constraints"] = list(reference.constraints)
        if "output_format" in reference.model_fields_set:
            explicit["output_format"] = reference.output_format
        if "document_ids" in reference.model_fields_set:
            explicit["selected_documents"] = [
                {"document_id": value} for value in reference.document_ids
            ]
    for slot, value in explicit.items():
        slots[slot] = make_resolved_slot(
            slot=slot,
            value=value,
            source="CONTEXT_REF",
            source_ref=f"turn:{turn.id}:task_context",
            confidence_rank=95,
        )
    natural_format = requested_format(turn.sanitized_input)
    if natural_format is not None and "output_format" not in explicit:
        slots["output_format"] = make_resolved_slot(
            slot="output_format",
            value=natural_format,
            source="INPUT",
            source_ref=f"turn:{turn.id}",
            confidence_rank=90,
        )
    if selected := slots.get("selected_documents"):
        if not isinstance(selected.value, list) or len(selected.value) > 4:
            raise ValueError("Invalid selected-document scope")
        documents: list[JsonValue] = []
        for value in selected.value:
            if not isinstance(value, dict) or not isinstance(value.get("document_id"), str):
                raise ValueError("Invalid selected-document reference")
            identifier = value.get("document_id")
            assert isinstance(identifier, str)
            document_id = UUID(identifier)
            decision = await PolicyEngine().evaluate_resource(
                session,
                ResourcePolicyInput(
                    principal=principal,
                    action="knowledge.read",
                    resource={"source": "document-index", "document_id": identifier},
                    context={"entrypoint": "task-context"},
                    risk_level=RiskLevel.L1,
                    resource_type="document",
                ),
            )
            if not principal.can("knowledge.read") or decision.effect != DecisionEffect.ALLOW:
                raise NotFoundError("Document", document_id)
            document, version = await KnowledgeService.get_document(session, principal, document_id)
            documents.append({"document_id": str(document.id), "version": version.version})
        slots["selected_documents"] = make_resolved_slot(
            slot=selected.slot,
            value=documents,
            source=selected.source,
            source_ref=selected.source_ref,
            confidence_rank=selected.confidence_rank,
        )
    return RunIntent.model_validate(
        {**intent.model_dump(), "resolved_slots": [item.model_dump() for item in slots.values()]}
    )
