"""Recheck managed provenance for generation, historical reads and delivery.

These current-access checkpoints are not a distributed revocation lock.
No upstream calls or credentials enter this path.
"""

from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import NotFoundError
from obsion.db.models import Document, Evidence, KnowledgeSyncItem, KnowledgeSyncSource
from obsion.domain.enums import ActorType, DecisionEffect, RiskLevel
from obsion.knowledge.evidence import document_bodies
from obsion.knowledge.lineage import run_evidence_lineage
from obsion.knowledge.service import KnowledgeService
from obsion.knowledge.source_access import MANAGED_KNOWLEDGE_SOURCE
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal
from obsion.security.policy import PolicyEngine, ResourcePolicyInput
from obsion.security.source_fence import acquire_source_publication_fence


class RunScoped(Protocol):
    @property
    def run_id(self) -> UUID | None: ...


async def filter_run_content[T: RunScoped](
    session: AsyncSession, principal: Principal, records: Iterable[T], *, stage: str
) -> list[T]:
    guard = KnowledgePublicationGuard()
    allowed: dict[UUID, bool] = {}
    result = []
    for record in records:
        if record.run_id is not None:
            if record.run_id not in allowed:
                allowed[record.run_id] = await guard.check(
                    session,
                    principal,
                    [],
                    run_id=record.run_id,
                    stage=stage,
                    link_policy_run=False,
                )
            if not allowed[record.run_id]:
                continue
        result.append(record)
    return result


class KnowledgePublicationGuard:
    def __init__(self, knowledge: KnowledgeService | None = None) -> None:
        self.knowledge = knowledge
        self.policy = PolicyEngine()
        self.audit = AuditWriter()

    async def check(
        self,
        session: AsyncSession,
        principal: Principal,
        evidence: list[Evidence],
        *,
        run_id: UUID,
        stage: str,
        expected_corp_id: str | None = None,
        allow_managed: bool = True,
        link_policy_run: bool = True,
    ) -> bool:
        if stage == "before_publication":
            await acquire_source_publication_fence(session, principal.organization_id)
        references: list[tuple[UUID, object, bool]] = []
        inherited = await run_evidence_lineage(session, principal.organization_id, run_id)
        invalid = inherited is None
        provided = {item.id for item in evidence}
        evidence = [*evidence, *(item for item in (inherited or []) if item.id not in provided)]
        for item in evidence:
            for body in document_bodies(item):
                metadata = body.metadata
                managed = metadata.get("source") == MANAGED_KNOWLEDGE_SOURCE
                try:
                    document_id = UUID(str(metadata.get("document_id")))
                except ValueError:
                    invalid |= managed
                    continue
                references.append((document_id, metadata.get("version"), managed))
        # Look up the authoritative type too: a missing source label must not
        # turn a managed document ID into an unrestricted snapshot.
        managed_ids = (
            set(
                await session.scalars(
                    select(Document.id).where(
                        Document.id.in_([item[0] for item in references]),
                        Document.organization_id == principal.organization_id,
                        Document.source == MANAGED_KNOWLEDGE_SOURCE,
                    )
                )
            )
            if references
            else set()
        )
        selected = [item for item in references if item[2] or item[0] in managed_ids]
        if not selected and not invalid:
            return True
        current = await load_principal_by_id(session, principal.organization_id, principal.id)
        decision = await self.policy.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=current,
                action="knowledge.read",
                resource={
                    "source": MANAGED_KNOWLEDGE_SOURCE,
                    "run_id": str(run_id),
                    **({"corp_id": expected_corp_id} if expected_corp_id is not None else {}),
                },
                context={"entrypoint": "knowledge-publication", "stage": stage},
                risk_level=RiskLevel.L1,
                resource_type="knowledge_source",
                run_id=run_id if link_policy_run else None,
            ),
        )
        allowed = (
            not invalid
            and allow_managed
            and current.can("knowledge.read")
            and decision.effect == DecisionEffect.ALLOW
        )
        for document_id, version, _ in selected:
            if not allowed:
                break
            try:
                document, current_version = await KnowledgeService.get_document(
                    session, current, document_id
                )
            except NotFoundError:
                allowed = False
                break
            if (
                document.source != MANAGED_KNOWLEDGE_SOURCE
                or type(version) is not int
                or version != current_version.version
            ):
                allowed = False
            if expected_corp_id is not None:
                corps = set(
                    await session.scalars(
                        select(KnowledgeSyncSource.corp_id)
                        .join(
                            KnowledgeSyncItem, KnowledgeSyncItem.source_id == KnowledgeSyncSource.id
                        )
                        .where(
                            KnowledgeSyncItem.document_id == document_id,
                            KnowledgeSyncSource.organization_id == principal.organization_id,
                        )
                    )
                )
                if corps != {expected_corp_id}:
                    allowed = False
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=run_id,
                actor_type=ActorType.USER,
                actor_id=current.id,
                action="knowledge.read",
                resource_type="knowledge_source",
                resource_id=str(run_id),
                outcome="AUTHORIZED" if allowed else "DENIED",
                policy_decision_id=decision.id,
                risk_level=RiskLevel.L1,
                metadata={"stage": stage, "managed_reference_count": len(selected)},
            ),
        )
        return allowed
