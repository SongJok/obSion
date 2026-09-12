from __future__ import annotations

import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import CreateTurnRequest, CreateWorkspaceRequest
from obsion.application.workspaces import WorkspaceService
from obsion.common.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.db.models import (
    ImConversationAudience,
    ImInboxEvent,
    ImInstallation,
    ImPrincipalBinding,
    Thread,
    User,
    Workspace,
    WorkspaceMember,
)
from obsion.domain.enums import ActorType, ImInboxStatus, ImIntent, ThreadStatus, Visibility
from obsion.harness.general import everyday_request
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal
from obsion.security.redaction import redact_text

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
        installation_id: UUID | None = None,
    ) -> ImPrincipalBinding:
        if not principal.can("identity.write"):
            raise AuthorizationError(
                "admin_access_denied", "Administration access is not permitted"
            )
        channel_name = _require_channel(channel)
        stable_sender = _require_sender_id(sender_id)
        if installation_id is not None:
            installation = await session.scalar(
                select(ImInstallation).where(
                    ImInstallation.id == installation_id,
                    ImInstallation.organization_id == principal.organization_id,
                    ImInstallation.channel == channel_name,
                    ImInstallation.active.is_(True),
                )
            )
            if installation is None:
                raise NotFoundError("active IM installation", installation_id)
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
                ImPrincipalBinding.installation_id == installation_id,
            )
        )
        now = utc_now()
        if binding is None:
            binding = ImPrincipalBinding(
                organization_id=principal.organization_id,
                channel=channel_name,
                sender_id=stable_sender,
                user_id=user.id,
                installation_id=installation_id,
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

    async def list_installations(
        self, session: AsyncSession, principal: Principal
    ) -> list[ImInstallation]:
        self._require_identity_admin(principal)
        result = await session.scalars(
            select(ImInstallation)
            .where(ImInstallation.organization_id == principal.organization_id)
            .order_by(ImInstallation.channel, ImInstallation.installation_id)
        )
        return list(result)

    async def install(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        channel: str,
        installation_id: str,
        corp_id: str,
        app_key: str,
    ) -> ImInstallation:
        self._require_identity_admin(principal)
        channel_name = _require_channel(channel)
        installed = _require_identifier(installation_id, "installation id")
        corp = _require_identifier(corp_id, "corp id")
        app = _require_identifier(app_key, "app key")
        existing = await session.scalar(
            select(ImInstallation)
            .where(ImInstallation.channel == channel_name)
            .where(
                (ImInstallation.installation_id == installed)
                | ((ImInstallation.corp_id == corp) & (ImInstallation.app_key == app))
            )
            .with_for_update()
        )
        if existing is not None and existing.organization_id != principal.organization_id:
            raise ConflictError(
                "idempotency_key_reused",
                "The IM installation is already bound to another organization",
            )
        now = utc_now()
        if existing is None:
            existing = ImInstallation(
                organization_id=principal.organization_id,
                channel=channel_name,
                installation_id=installed,
                corp_id=corp,
                app_key=app,
                active=True,
                created_by=principal.id,
                provider=channel_name,
                external_corp_id=corp,
                external_app_id=app,
                adapter_principal_id=principal.id,
                status="ACTIVE",
                verification_source="identity-admin",
            )
            session.add(existing)
            action = "identity.im.installation.create"
        elif (
            existing.installation_id != installed
            or existing.corp_id != corp
            or existing.app_key != app
        ):
            raise ConflictError(
                "idempotency_key_reused",
                "The IM installation identity does not match its existing binding",
            )
        else:
            existing.active = True
            existing.revoked_at = None
            existing.status = "ACTIVE"
            existing.updated_at = now
            action = "identity.im.installation.restore"
        await session.flush()
        await self._audit_identity(
            session,
            principal,
            action=action,
            resource_type="im_installation",
            resource_id=str(existing.id),
            metadata={"channel": channel_name, "installation_id": installed, "corp_id": corp},
        )
        return existing

    async def revoke_installation(
        self, session: AsyncSession, principal: Principal, installation_id: UUID
    ) -> ImInstallation:
        self._require_identity_admin(principal)
        installation = await session.scalar(
            select(ImInstallation)
            .where(
                ImInstallation.id == installation_id,
                ImInstallation.organization_id == principal.organization_id,
            )
            .with_for_update()
        )
        if installation is None:
            raise NotFoundError("IM installation", installation_id)
        installation.active = False
        installation.revoked_at = utc_now()
        installation.status = "REVOKED"
        await session.flush()
        await self._audit_identity(
            session,
            principal,
            action="identity.im.installation.revoke",
            resource_type="im_installation",
            resource_id=str(installation.id),
            metadata={
                "channel": installation.channel,
                "installation_id": installation.installation_id,
            },
        )
        return installation

    async def list_audiences(
        self, session: AsyncSession, principal: Principal
    ) -> list[ImConversationAudience]:
        self._require_identity_admin(principal)
        result = await session.scalars(
            select(ImConversationAudience)
            .where(ImConversationAudience.organization_id == principal.organization_id)
            .order_by(ImConversationAudience.conversation_id)
        )
        return list(result)

    async def bind_conversation_audience(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        installation_id: UUID,
        conversation_id: str,
        workspace_id: UUID,
    ) -> ImConversationAudience:
        self._require_identity_admin(principal)
        conversation = _require_identifier(conversation_id, "conversation id")
        installation = await session.scalar(
            select(ImInstallation).where(
                ImInstallation.id == installation_id,
                ImInstallation.organization_id == principal.organization_id,
                ImInstallation.active.is_(True),
            )
        )
        if installation is None:
            raise NotFoundError("active IM installation", installation_id)
        workspace = await session.scalar(
            select(Workspace.id).where(
                Workspace.id == workspace_id,
                Workspace.organization_id == principal.organization_id,
                Workspace.archived_at.is_(None),
            )
        )
        if workspace is None:
            raise NotFoundError("Workspace", workspace_id)
        audience = await session.scalar(
            select(ImConversationAudience)
            .where(
                ImConversationAudience.installation_id == installation.id,
                ImConversationAudience.conversation_id == conversation,
            )
            .with_for_update()
        )
        if audience is None:
            audience = ImConversationAudience(
                organization_id=principal.organization_id,
                installation_id=installation.id,
                conversation_id=conversation,
                workspace_id=workspace_id,
                active=True,
                created_by=principal.id,
            )
            session.add(audience)
            action = "identity.im.audience.create"
        else:
            audience.workspace_id = workspace_id
            audience.active = True
            audience.revoked_at = None
            audience.updated_at = utc_now()
            action = "identity.im.audience.replace"
        await session.flush()
        await self._audit_identity(
            session,
            principal,
            action=action,
            resource_type="im_conversation_audience",
            resource_id=str(audience.id),
            metadata={"installation_id": str(installation.id), "conversation_id": conversation},
        )
        return audience

    async def revoke_conversation_audience(
        self, session: AsyncSession, principal: Principal, audience_id: UUID
    ) -> ImConversationAudience:
        self._require_identity_admin(principal)
        audience = await session.scalar(
            select(ImConversationAudience)
            .where(
                ImConversationAudience.id == audience_id,
                ImConversationAudience.organization_id == principal.organization_id,
            )
            .with_for_update()
        )
        if audience is None:
            raise NotFoundError("IM conversation audience", audience_id)
        audience.active = False
        audience.revoked_at = utc_now()
        await session.flush()
        await self._audit_identity(
            session,
            principal,
            action="identity.im.audience.revoke",
            resource_type="im_conversation_audience",
            resource_id=str(audience.id),
            metadata={"conversation_id": audience.conversation_id},
        )
        return audience

    async def accept_trusted_event(
        self,
        session: AsyncSession,
        actor: Principal,
        *,
        channel: str,
        installation_id: str,
        corp_id: str,
        app_key: str,
        vendor_event_id: str,
        sender_id: str,
        conversation_id: str,
        text: str,
        is_group: bool,
    ) -> dict[str, object]:
        """Persist a verified vendor callback before background dispatch.

        The adapter authenticates the vendor request. This service establishes the
        tenant and subject from durable administrator-owned mappings, then makes
        replay behavior deterministic before returning an acknowledgement.
        """
        if not actor.can("im.delegate"):
            raise AuthorizationError("im_delegate_denied", "IM sender delegation is not permitted")
        channel_name = _require_channel(channel)
        installed = _require_identifier(installation_id, "installation id")
        corp = _require_identifier(corp_id, "corp id")
        app = _require_identifier(app_key, "app key")
        vendor_id = _require_identifier(vendor_event_id, "vendor event id")
        stable_sender = _require_sender_id(sender_id)
        conversation = _require_identifier(conversation_id, "conversation id")
        question = text.strip()
        if not question:
            raise ValidationError("im_sender_id_required", "An IM event requires non-empty text")
        intent, intent_reason = _classify_intent(question)
        sanitized_question = redact_text(question)
        installation = await session.scalar(
            select(ImInstallation)
            .where(
                ImInstallation.organization_id == actor.organization_id,
                ImInstallation.channel == channel_name,
                ImInstallation.installation_id == installed,
                ImInstallation.corp_id == corp,
                ImInstallation.app_key == app,
                ImInstallation.active.is_(True),
            )
            .with_for_update()
        )
        if installation is None:
            raise AuthorizationError(
                "unknown_im_sender",
                "The IM installation is not mapped to this organization",
                channel=channel_name,
            )
        binding = await session.scalar(
            select(ImPrincipalBinding).where(
                ImPrincipalBinding.organization_id == actor.organization_id,
                ImPrincipalBinding.channel == channel_name,
                ImPrincipalBinding.sender_id == stable_sender,
                ImPrincipalBinding.installation_id == installation.id,
                ImPrincipalBinding.active.is_(True),
            )
        )
        if binding is None:
            raise AuthorizationError(
                "unknown_im_sender",
                "The IM sender is not bound to a provisioned principal",
                channel=channel_name,
            )
        fingerprint = _event_fingerprint(
            channel_name,
            installed,
            corp,
            app,
            vendor_id,
            stable_sender,
            conversation,
            sanitized_question,
            is_group,
        )
        existing = await session.scalar(
            select(ImInboxEvent)
            .where(
                ImInboxEvent.installation_id == installation.id,
                ImInboxEvent.vendor_event_id == vendor_id,
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.payload_fingerprint != fingerprint:
                raise ConflictError(
                    "idempotency_key_reused",
                    "The vendor event id was replayed with different content",
                )
            return _accepted_event(existing, duplicate=True)
        audience = None
        if is_group:
            audience = await session.scalar(
                select(ImConversationAudience).where(
                    ImConversationAudience.organization_id == actor.organization_id,
                    ImConversationAudience.installation_id == installation.id,
                    ImConversationAudience.conversation_id == conversation,
                    ImConversationAudience.active.is_(True),
                )
            )
        event = ImInboxEvent(
            organization_id=actor.organization_id,
            installation_id=installation.id,
            binding_id=binding.id,
            audience_id=audience.id if audience is not None else None,
            accepted_by=actor.id,
            channel=channel_name,
            vendor_event_id=vendor_id,
            payload_fingerprint=fingerprint,
            sender_id=stable_sender,
            conversation_id=conversation,
            is_group=is_group,
            intent=intent,
            text=sanitized_question,
            event_metadata={
                "corp_id": corp,
                "app_key": app,
                "installation_id": installed,
                "subject_user_id": str(binding.user_id),
                "intent_reason": intent_reason,
            },
            status=ImInboxStatus.PENDING,
            attempt_count=0,
        )
        session.add(event)
        await session.flush()
        await self._audit_identity(
            session,
            actor,
            action="identity.im.inbox.accept",
            resource_type="im_inbox_event",
            resource_id=str(event.id),
            metadata={
                "channel": channel_name,
                "installation_id": installed,
                "vendor_event_id": vendor_id,
                "is_group": is_group,
                "intent": intent,
                "intent_reason": intent_reason,
            },
        )
        return _accepted_event(event, duplicate=False)

    async def get_inbox_status(
        self, session: AsyncSession, principal: Principal, event_id: UUID
    ) -> dict[str, object]:
        event = await session.scalar(
            select(ImInboxEvent).where(
                ImInboxEvent.id == event_id,
                ImInboxEvent.organization_id == principal.organization_id,
            )
        )
        if event is None or (
            not principal.can("im.delegate")
            and event.event_metadata.get("subject_user_id") != str(principal.id)
        ):
            raise NotFoundError("IM Inbox event", event_id)
        return _accepted_event(event, duplicate=False)

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
                ImPrincipalBinding.installation_id.is_(None),
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

    async def dispatch_inbox_event(self, session: AsyncSession, event: ImInboxEvent) -> UUID:
        """Create a user-owned private Harness Run after an Inbox lease is claimed."""
        installation = await session.scalar(
            select(ImInstallation)
            .where(
                ImInstallation.id == event.installation_id,
                ImInstallation.organization_id == event.organization_id,
                ImInstallation.channel == event.channel,
                ImInstallation.active.is_(True),
            )
            .with_for_update()
        )
        if installation is None:
            raise AuthorizationError("unknown_im_sender", "The IM installation is no longer active")
        # Resolve the durable binding while the Inbox lease is held; a binding id
        # is intentionally not interchangeable with a User id.
        binding = await session.scalar(
            select(ImPrincipalBinding).where(
                ImPrincipalBinding.id == event.binding_id,
                ImPrincipalBinding.installation_id == event.installation_id,
                ImPrincipalBinding.channel == event.channel,
                ImPrincipalBinding.sender_id == event.sender_id,
                ImPrincipalBinding.user_id == UUID(str(event.event_metadata["subject_user_id"])),
                ImPrincipalBinding.organization_id == event.organization_id,
                ImPrincipalBinding.active.is_(True),
            )
        )
        if binding is None:
            raise AuthorizationError(
                "unknown_im_sender", "The IM sender binding is no longer active"
            )
        subject = await load_principal_by_id(session, event.organization_id, binding.user_id)
        service_actor = subject
        workspace = await self._ensure_workspace(session, service_actor, subject)
        digest = hashlib.sha256(
            f"{event.channel}\0{event.installation_id}\0{event.conversation_id}\0{subject.id}".encode()
        ).hexdigest()
        thread = await self._ensure_thread(
            session, subject, workspace.id, f"im:{event.channel}:{digest}"
        )
        turn, run = await self.workspaces.create_turn(
            session,
            subject,
            thread.id,
            CreateTurnRequest(
                input=event.text,
                context_refs=[
                    {
                        "type": "im_delivery",
                        "channel": event.channel,
                        "conversation_id": event.conversation_id,
                        "sender_id": event.sender_id,
                        "binding_id": str(event.binding_id),
                        "installation_id": str(event.installation_id),
                        "inbox_event_id": str(event.id),
                        "group_status_only": event.is_group,
                        "intent": event.intent,
                    }
                ],
            ),
        )
        del turn
        event.run_id = run.id
        event.status = ImInboxStatus.ACCEPTED
        event.lease_owner = None
        event.lease_expires_at = None
        event.processed_at = utc_now()
        await self._audit_identity(
            session,
            subject,
            action="identity.im.inbox.dispatch",
            resource_type="im_inbox_event",
            resource_id=str(event.id),
            metadata={"run_id": str(run.id), "is_group": event.is_group},
        )
        return run.id

    def _require_identity_admin(self, principal: Principal) -> None:
        if not principal.can("identity.write"):
            raise AuthorizationError(
                "admin_access_denied", "Administration access is not permitted"
            )

    async def _audit_identity(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        action: str,
        resource_type: str,
        resource_id: str,
        metadata: dict[str, object],
    ) -> None:
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=new_id(),
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome="SUCCESS",
                metadata=metadata,
            ),
        )

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


def _require_identifier(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValidationError("im_sender_id_required", f"A stable IM {label} is required")
    return normalized


def _event_fingerprint(*values: object) -> str:
    canonical = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _classify_intent(text: str) -> tuple[ImIntent, str]:
    """Conservatively classify intent without turning labels into authorization."""
    normalized = " ".join(text.casefold().split())
    if everyday_request(text, context_refs=[]):
        return ImIntent.QUERY, "text_only:general"
    markers = (
        (ImIntent.CREATE, ("创建", "新建", "新增", "写一份", "create ", "add ")),
        (ImIntent.SUMMARY, ("总结", "摘要", "概括", "summarize", "summary")),
        (ImIntent.ANALYSIS, ("分析", "排查", "诊断", "为什么", "原因", "analyze", "diagnose")),
    )
    for intent, candidates in markers:
        if any(marker in normalized for marker in candidates):
            return intent, f"marker:{intent.value.casefold()}"
    return ImIntent.QUERY, "default:query"


def _accepted_event(event: ImInboxEvent, *, duplicate: bool) -> dict[str, object]:
    return {
        "inbox_event_id": event.id,
        "status": event.status,
        "duplicate": duplicate,
        "run_id": event.run_id,
        "safe_status": {
            ImInboxStatus.PENDING: "received",
            ImInboxStatus.PROCESSING: "processing",
            ImInboxStatus.ACCEPTED: "dispatched",
            ImInboxStatus.REJECTED: "rejected",
        }[event.status],
        "intent": event.intent,
    }
