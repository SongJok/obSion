"""Revalidate locally indexed source versions from Gateway-created provenance."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import NotFoundError
from obsion.db.models import CapabilityDefinition, CapabilityVersion, Evidence
from obsion.domain.enums import ActorType, CapabilityTransport, DecisionEffect, RiskLevel
from obsion.knowledge.evidence import document_bodies
from obsion.knowledge.service import KnowledgeService
from obsion.knowledge.source_access import MANAGED_KNOWLEDGE_SOURCE
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal
from obsion.security.policy import PolicyEngine, ResourcePolicyInput


async def check_indexed_documents(
    session: AsyncSession,
    principal: Principal,
    evidence: list[Evidence],
    *,
    run_id: UUID,
    stage: str,
    link_policy_run: bool,
) -> bool:
    """Provider document IDs are not local IDs merely because they look like UUIDs.

    Only the immutable, organization-scoped INTERNAL capability version recorded
    by Gateway establishes a local index read. Managed sources retain their
    additional tenant checks in KnowledgePublicationGuard.
    """
    versions: dict[UUID, UUID] = {}
    for item in evidence:
        try:
            versions[item.id] = UUID(str(item.lineage.get("capability_version_id")))
        except (AttributeError, ValueError):
            continue
    if not versions:
        return True
    indexed = set(
        await session.scalars(
            select(CapabilityVersion.id)
            .join(CapabilityDefinition, CapabilityDefinition.id == CapabilityVersion.capability_id)
            .where(
                CapabilityVersion.organization_id == principal.organization_id,
                CapabilityDefinition.organization_id == principal.organization_id,
                CapabilityVersion.id.in_(set(versions.values())),
                CapabilityVersion.transport == CapabilityTransport.INTERNAL,
                CapabilityDefinition.name.in_(
                    {"knowledge.search", "document.read", "ticket.search"}
                ),
            )
        )
    )
    references: set[tuple[UUID, int]] = set()
    valid = True
    for item in evidence:
        if versions.get(item.id) not in indexed:
            continue
        metadata = [body.metadata for body in document_bodies(item)]
        # Even a document with no readable chunks remains a selected source.
        if item.content.get("operation") == "document.read":
            metadata.append(item.content)
        for reference in metadata:
            try:
                document_id = UUID(str(reference.get("document_id")))
            except ValueError:
                valid = False
                continue
            version = reference.get("version")
            if type(version) is not int or version < 1:
                valid = False
                continue
            references.add((document_id, version))
    if not references and valid:
        return True
    current = await load_principal_by_id(session, principal.organization_id, principal.id)
    policy = PolicyEngine()
    audit = AuditWriter()
    for document_id, version in sorted(references, key=lambda item: (str(item[0]), item[1])):
        decision = await policy.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=current,
                action="knowledge.read",
                resource={
                    "source": "document-index",
                    "document_id": str(document_id),
                    "run_id": str(run_id),
                },
                context={"entrypoint": "knowledge-publication", "stage": stage},
                risk_level=RiskLevel.L1,
                resource_type="document",
                run_id=run_id if link_policy_run else None,
            ),
        )
        allowed = current.can("knowledge.read") and decision.effect == DecisionEffect.ALLOW
        managed = False
        if allowed:
            try:
                document, latest = await KnowledgeService.get_document(
                    session, current, document_id
                )
            except NotFoundError:
                allowed = False
            else:
                managed = document.source == MANAGED_KNOWLEDGE_SOURCE
                allowed = version == latest.version
        valid = valid and allowed
        await audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=run_id,
                actor_type=ActorType.USER,
                actor_id=current.id,
                action="knowledge.read",
                resource_type="document",
                resource_id=str(document_id),
                outcome="AUTHORIZED" if allowed else "DENIED",
                policy_decision_id=decision.id,
                risk_level=RiskLevel.L1,
                metadata={"stage": stage, "version": version, "managed": managed},
            ),
        )
    if not references and not valid:
        await audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=run_id,
                actor_type=ActorType.USER,
                actor_id=current.id,
                action="knowledge.read",
                resource_type="document",
                resource_id=str(run_id),
                outcome="DENIED",
                metadata={"stage": stage, "reason": "indexed_provenance_invalid"},
            ),
        )
    return valid
