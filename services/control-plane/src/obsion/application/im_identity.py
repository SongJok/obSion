from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import CreateTurnRequest, CreateWorkspaceRequest
from obsion.application.workspaces import WorkspaceService
from obsion.common.errors import AuthorizationError, NotFoundError, ValidationError
from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.db.models import ImPrincipalBinding, Thread, User, Workspace, WorkspaceMember
from obsion.domain.enums import ActorType, ThreadStatus, Visibility
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal

IM_WORKSPACE_NAME = "IM"
ALLOWED_IM_CHANNELS = frozenset({"development", "feishu", "dingtalk", "wecom"})


class ImIdentityService:
    """Binds stable IM sender ids to Users. Display names never authorize."""

    def __init__(self, workspaces: WorkspaceService) -> None:
        self.workspaces = workspaces
        self.audit = AuditWriter()

    async def list_bindings(
        self, session: AsyncSession, principal: Principal
    ) -> list[ImPrincipalBinding]:
        if not principal.can("identity.write") and not principal.can("admin.read"):
            raise AuthorizationError(
                "admin_access_denied", "Administration access is not permitted"
            )
        result = await session.scalars(
            select(ImPrincipalBinding)
            .where(ImPrincipalBinding.organization_id == principal.organization_id)
            .order_by(ImPrincipalBinding.channel, ImPrincipalBinding.sender_id)
        )
        return list(result)

    async def bind(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        channel: str,
        sender_id: str,
        user_id: UUID,
    ) -> ImPrincipalBinding:
        if not principal.can("identity.write"):
            raise AuthorizationError(
                "admin_access_denied", "Administration access is not permitted"
            )
        channel_name = _require_channel(channel)
        stable_sender = _require_sender_id(sender_id)
        user = await session.scalar(
            select(User).where(
                User.id == user_id,
                User.organization_id == principal.organization_id,
                User.active.is_(True),
            )
        )
        if user is None:
            raise NotFoundError("User", user_id)
        binding = await session.scalar(
            select(ImPrincipalBinding).where(
                ImPrincipalBinding.organization_id == principal.organization_id,
                ImPrincipalBinding.channel == channel_name,
                ImPrincipalBinding.sender_id == stable_sender,
            )
        )
        now = utc_now()
        if binding is None:
            binding = ImPrincipalBinding(
                organization_id=principal.organization_id,
                channel=channel_name,
                sender_id=stable_sender,
                user_id=user.id,
                active=True,
                created_by=principal.id,
            )
            session.add(binding)
            action = "identity.im.binding.create"
        else:
            binding.user_id = user.id
            binding.active = True
            binding.revoked_at = None
            binding.updated_at = now
            action = "identity.im.binding.replace"
        try:
            await session.flush()
        except IntegrityError as exc:
            raise NotFoundError("User", user_id) from exc
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=new_id(),
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action=action,
                resource_type="im_principal_binding",
                resource_id=str(binding.id),
                outcome="SUCCESS",
                metadata={
                    "channel": channel_name,
                    "sender_id": stable_sender,
                    "user_id": str(user.id),
                },
            ),
        )
        return binding

    async def revoke(
        self, session: AsyncSession, principal: Principal, binding_id: UUID
    ) -> ImPrincipalBinding:
        if not principal.can("identity.write"):
            raise AuthorizationError(
                "admin_access_denied", "Administration access is not permitted"
            )
        binding = await session.scalar(
            select(ImPrincipalBinding).where(
                ImPrincipalBinding.id == binding_id,
                ImPrincipalBinding.organization_id == principal.organization_id,
            )
        )
        if binding is None:
            raise NotFoundError("IM principal binding", binding_id)
        binding.active = False
        binding.revoked_at = utc_now()
        await session.flush()
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=new_id(),
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action="identity.im.binding.revoke",
                resource_type="im_principal_binding",
                resource_id=str(binding.id),
                outcome="SUCCESS",
                metadata={"channel": binding.channel, "sender_id": binding.sender_id},
            ),
        )
        return binding

    async def ingest_message(
        self,
        session: AsyncSession,
        actor: Principal,
        *,
        channel: str,
        sender_id: str,
        conversation_id: str,
        text: str,
    ) -> dict[str, str]:
        if not actor.can("im.delegate"):
            raise AuthorizationError("im_delegate_denied", "IM sender delegation is not permitted")
        channel_name = _require_channel(channel)
        stable_sender = _require_sender_id(sender_id)
        conversation = conversation_id.strip()
        question = text.strip()
        if not conversation or not question:
            raise ValidationError(
                "im_sender_id_required",
                "A stable IM sender id, conversation id, and text are required",
            )
        binding = await session.scalar(
            select(ImPrincipalBinding).where(
                ImPrincipalBinding.organization_id == actor.organization_id,
                ImPrincipalBinding.channel == channel_name,
                ImPrincipalBinding.sender_id == stable_sender,
                ImPrincipalBinding.active.is_(True),
            )
        )
        if binding is None:
            raise AuthorizationError(
                "unknown_im_sender",
                "The IM sender is not bound to a provisioned principal",
                channel=channel_name,
            )
        subject = await load_principal_by_id(session, actor.organization_id, binding.user_id)
        workspace = await self._ensure_workspace(session, actor, subject)
        conversation_digest = hashlib.sha256(f"{channel_name}\0{conversation}".encode()).hexdigest()
        thread_title = f"im:{channel_name}:{conversation_digest}"
        thread = await self._ensure_thread(session, subject, workspace.id, thread_title)
        turn, run = await self.workspaces.create_turn(
            session,
            subject,
            thread.id,
            CreateTurnRequest(
                input=question,
                context_refs=[
                    {
                        "type": "im_delivery",
                        "channel": channel_name,
                        "conversation_id": conversation,
                        "sender_id": stable_sender,
                        "binding_id": str(binding.id),
                    }
                ],
            ),
        )
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=actor.organization_id,
                correlation_id=new_id(),
                actor_type=ActorType.USER,
                actor_id=actor.id,
                action="identity.im.delegate",
                resource_type="run",
                resource_id=str(run.id),
                outcome="SUCCESS",
                metadata={
                    "channel": channel_name,
                    "sender_id": stable_sender,
                    "subject_user_id": str(subject.id),
                    "thread_id": str(thread.id),
                },
            ),
        )
        return {
            "binding_id": str(binding.id),
            "channel": channel_name,
            "principal_id": str(subject.id),
            "run_id": str(run.id),
            "sender_id": stable_sender,
            "thread_id": str(thread.id),
            "turn_id": str(turn.id),
            "workspace_id": str(workspace.id),
        }

    async def _ensure_workspace(
        self, session: AsyncSession, actor: Principal, subject: Principal
    ) -> Workspace:
        workspace = await session.scalar(
            select(Workspace).where(
                Workspace.organization_id == subject.organization_id,
                Workspace.owner_id == subject.id,
                Workspace.name == IM_WORKSPACE_NAME,
                Workspace.archived_at.is_(None),
            )
        )
        if workspace is None:
            workspace = await self.workspaces.create_workspace(
                session,
                subject,
                CreateWorkspaceRequest(
                    name=IM_WORKSPACE_NAME,
                    description="Obsion Experience IM workspace",
                    visibility=Visibility.PRIVATE,
                ),
            )
        await self._ensure_member(
            session, workspace, subject.id, can_write=True, created_by=actor.id
        )
        if actor.id != subject.id:
            await self._ensure_member(
                session, workspace, actor.id, can_write=False, created_by=actor.id
            )
        return workspace

    async def _ensure_thread(
        self,
        session: AsyncSession,
        subject: Principal,
        workspace_id: UUID,
        title: str,
    ) -> Thread:
        threads = await self.workspaces.list_threads(
            session, subject, workspace_id, include_archived=True
        )
        for thread in threads:
            if thread.title != title:
                continue
            if thread.status == ThreadStatus.ARCHIVED:
                return await self.workspaces.resume_thread(session, subject, thread.id)
            return thread
        return await self.workspaces.create_thread(session, subject, workspace_id, title)

    async def _ensure_member(
        self,
        session: AsyncSession,
        workspace: Workspace,
        user_id: UUID,
        *,
        can_write: bool,
        created_by: UUID,
    ) -> None:
        if workspace.owner_id == user_id:
            return
        member = await session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == user_id,
            )
        )
        permissions = ["read", "write"] if can_write else ["read"]
        if member is None:
            session.add(
                WorkspaceMember(
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.id,
                    user_id=user_id,
                    permissions=permissions,
                    can_write=can_write,
                    created_by=created_by,
                    created_at=utc_now(),
                )
            )
            await session.flush()
            return
        if can_write and not member.can_write:
            member.permissions = permissions
            member.can_write = True
            await session.flush()


def _require_channel(channel: str) -> str:
    name = channel.strip().lower()
    if name not in ALLOWED_IM_CHANNELS:
        raise ValidationError(
            "im_sender_id_required",
            "IM channel is not a supported identity namespace",
        )
    return name


def _require_sender_id(sender_id: str) -> str:
    value = sender_id.strip()
    if not value:
        raise ValidationError(
            "im_sender_id_required",
            "A stable IM sender id is required. Display names cannot authorize.",
        )
    return value
