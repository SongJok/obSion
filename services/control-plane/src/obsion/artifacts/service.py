import hashlib
from contextlib import suppress
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.artifacts.store import ObjectStore, StoredObject
from obsion.common.errors import AuthorizationError, NotFoundError, ObsionError, ValidationError
from obsion.common.ids import new_id
from obsion.db.models import Artifact, Run, Thread, Turn
from obsion.domain.enums import ActorType, ArtifactKind, Classification
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore
from obsion.security.identity import Principal
from obsion.security.workspace_access import require_run_access, require_workspace_access


class ArtifactService:
    def __init__(self, store: ObjectStore, *, max_upload_bytes: int) -> None:
        self.store = store
        self.max_upload_bytes = max_upload_bytes
        self.events = EventStore()
        self.audit = AuditWriter()

    async def create_file(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        workspace_id: UUID,
        run_id: UUID | None,
        kind: ArtifactKind,
        title: str,
        media_type: str,
        content: bytes,
        classification: Classification,
        lineage: dict[str, Any],
    ) -> Artifact:
        if not principal.can("artifact.write"):
            raise AuthorizationError("artifact_write_denied", "Artifact upload is not permitted")
        await require_workspace_access(session, principal, workspace_id, write=True)
        if not content:
            raise ValidationError("artifact_empty", "Artifact content cannot be empty")
        if len(content) > self.max_upload_bytes:
            raise ValidationError(
                "artifact_too_large",
                "The artifact exceeds the configured upload limit",
                max_bytes=self.max_upload_bytes,
            )
        if run_id is not None:
            run = await require_run_access(session, principal, run_id, write=True)
            run_workspace_id = await session.scalar(
                select(Thread.workspace_id)
                .join(Turn, Turn.thread_id == Thread.id)
                .join(Run, Run.turn_id == Turn.id)
                .where(Run.id == run.id)
            )
            if run_workspace_id != workspace_id:
                raise ValidationError(
                    "artifact_run_workspace_mismatch",
                    "Artifact run and workspace do not match",
                )
        artifact_id = new_id()
        key = f"{principal.organization_id}/{workspace_id}/{artifact_id}"
        checksum = hashlib.sha256(content).hexdigest()
        artifact = Artifact(
            id=artifact_id,
            organization_id=principal.organization_id,
            workspace_id=workspace_id,
            run_id=run_id,
            kind=kind,
            title=title.strip(),
            media_type=media_type,
            storage_key=key,
            checksum_sha256=checksum,
            classification=classification,
            acl={"workspace_id": str(workspace_id)},
            lineage=lineage,
        )
        session.add(artifact)
        await session.flush()
        stored = False
        try:
            await self.store.put(
                key,
                content,
                media_type=media_type,
                metadata={"sha256": checksum, "classification": classification.value},
            )
            stored = True
            await self.events.append(
                session,
                EventDraft(
                    name="artifact.created",
                    aggregate_type="artifact",
                    aggregate_id=artifact.id,
                    organization_id=principal.organization_id,
                    correlation_id=run_id or artifact.id,
                    actor_type=ActorType.USER,
                    actor_id=principal.id,
                    run_id=run_id,
                    payload={"artifact_id": str(artifact.id), "kind": artifact.kind},
                ),
            )
            await self.audit.write(
                session,
                AuditDraft(
                    organization_id=principal.organization_id,
                    correlation_id=run_id or artifact.id,
                    actor_type=ActorType.USER,
                    actor_id=principal.id,
                    action="artifact.create",
                    resource_type="artifact",
                    resource_id=str(artifact.id),
                    outcome="SUCCESS",
                    metadata={"kind": artifact.kind, "bytes": len(content)},
                ),
            )
        except Exception:
            if stored:
                with suppress(ObsionError):
                    await self.store.delete(key)
            raise
        return artifact

    async def list_workspace(
        self, session: AsyncSession, principal: Principal, workspace_id: UUID
    ) -> list[Artifact]:
        await require_workspace_access(session, principal, workspace_id)
        return list(
            await session.scalars(
                select(Artifact)
                .where(
                    Artifact.organization_id == principal.organization_id,
                    Artifact.workspace_id == workspace_id,
                )
                .order_by(Artifact.created_at.desc())
                .limit(500)
            )
        )

    async def get_metadata(
        self, session: AsyncSession, principal: Principal, artifact_id: UUID
    ) -> Artifact:
        artifact = await session.scalar(
            select(Artifact).where(
                Artifact.id == artifact_id,
                Artifact.organization_id == principal.organization_id,
            )
        )
        if artifact is None:
            raise NotFoundError("Artifact", artifact_id)
        await require_workspace_access(session, principal, artifact.workspace_id)
        return artifact

    async def content(
        self, session: AsyncSession, principal: Principal, artifact_id: UUID
    ) -> tuple[Artifact, StoredObject]:
        artifact = await self.get_metadata(session, principal, artifact_id)
        if artifact.storage_key is None:
            raise ValidationError(
                "artifact_inline_only", "This artifact has no downloadable binary content"
            )
        stored = await self.store.get(artifact.storage_key)
        if hashlib.sha256(stored.data).hexdigest() != artifact.checksum_sha256:
            raise ObsionError(
                "artifact_integrity_failed",
                "Artifact content failed integrity verification",
                status_code=503,
            )
        return artifact, stored
