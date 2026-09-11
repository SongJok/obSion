"""Trusted IM admission and explicit, transaction-local processing.

Callers own the transaction. There is no network effect, worker lease or commit
inside this service. A database write lock serializes each installation, including
revocation; it works on PostgreSQL and SQLite, not a process-local mutex.
"""

import hashlib
import json
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.im_inbox_schemas import (
    CreateImGroupAudience,
    CreateImInstallation,
    CreateInstallationBinding,
    TrustedImInbound,
)
from obsion.api.schemas import CreateTurnRequest, CreateWorkspaceRequest
from obsion.application.workspaces import WorkspaceService
from obsion.capabilities.dingtalk_robot import DINGTALK_ROBOT_PROTOCOL, ORIGIN
from obsion.common.errors import AuthorizationError, ConflictError, NotFoundError
from obsion.common.ids import new_id
from obsion.common.time import ensure_utc, utc_now
from obsion.db.im_models import (
    ImConversationBinding,
    ImGroupAudience,
    ImInboxMessage,
    ImInstallationBinding,
)
from obsion.db.models import (
    Connector,
    ImInstallation,
    Organization,
    Thread,
    User,
    Workspace,
    WorkspaceMember,
)
from obsion.domain.enums import (
    ActorType,
    ConnectorStatus,
    DecisionEffect,
    RiskLevel,
    ThreadStatus,
    Visibility,
)
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal
from obsion.security.policy import PolicyEngine, ResourcePolicyInput
from obsion.security.redaction import redact_text
from obsion.security.workspace_access import require_workspace_access


def _installation_connector_allowed(connector: Connector, provider: str | None) -> bool:
    if connector.connector_type == f"{provider}-docs":
        return True
    return (
        provider == "dingtalk"
        and connector.connector_type == "dingtalk-robot"
        and isinstance(connector.configuration, dict)
        and connector.configuration.get("protocol") == DINGTALK_ROBOT_PROTOCOL
        and connector.endpoint == ORIGIN
        and ORIGIN in connector.allowed_egress
        and "im.reply.deliver" in connector.declared_grants
        and bool(connector.credential_ref)
    )


class ImInboxService:
    def __init__(self, workspaces: WorkspaceService) -> None:
        self.workspaces = workspaces
        self.policy = PolicyEngine()
        self.audit = AuditWriter()

    async def _authorize(
        self,
        session: AsyncSession,
        actor: Principal,
        action: str,
        installation_id: UUID | None = None,
    ) -> Principal:
        # Refresh grants for service calls as well as REST. Never trust payload roles.
        current = await load_principal_by_id(session, actor.organization_id, actor.id)
        organization = await session.scalar(
            select(Organization).where(
                Organization.id == current.organization_id, Organization.active.is_(True)
            )
        )
        if organization is None:
            raise AuthorizationError("im_delegate_denied", "The organization is inactive")
        decision = await self.policy.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=current,
                action=action,
                resource_type="im_installation",
                resource={"installation_id": str(installation_id) if installation_id else None},
                context={"entrypoint": "trusted-im-inbox"},
                risk_level=RiskLevel.L1,
            ),
        )
        if decision.effect != DecisionEffect.ALLOW or decision.obligations:
            raise AuthorizationError("im_delegate_denied", "IM operation is not permitted")
        return current

    async def _audit(
        self,
        session: AsyncSession,
        actor: Principal,
        action: str,
        resource_id: UUID,
        *,
        outcome: str = "SUCCESS",
    ) -> None:
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=actor.organization_id,
                correlation_id=new_id(),
                actor_type=ActorType.USER,
                actor_id=actor.id,
                action=action,
                resource_type="im_inbox",
                resource_id=str(resource_id),
                outcome=outcome,
                metadata={},
            ),
        )

    async def _installation(
        self,
        session: AsyncSession,
        actor: Principal,
        installation_id: UUID,
        *,
        lock: bool = True,
    ) -> ImInstallation:
        predicate = (
            ImInstallation.id == installation_id,
            ImInstallation.organization_id == actor.organization_id,
        )
        if lock:
            # Acquire the write lock before reads. SQLite has no SELECT FOR UPDATE.
            await session.execute(
                update(ImInstallation)
                .where(*predicate)
                .values(status=ImInstallation.status, updated_at=ImInstallation.updated_at)
            )
        installation = await session.scalar(
            select(ImInstallation).where(*predicate).execution_options(populate_existing=True)
        )
        if installation is None:
            raise NotFoundError("IM installation", installation_id)
        return installation

    async def _adapter(
        self,
        session: AsyncSession,
        actor: Principal,
        installation: ImInstallation,
    ) -> Principal:
        if installation.status != "ACTIVE" or installation.adapter_principal_id != actor.id:
            raise AuthorizationError("im_delegate_denied", "Installation adapter is not authorized")
        current = await self._authorize(session, actor, "im.delegate", installation.id)
        connector = await session.scalar(
            select(Connector).where(
                Connector.id == installation.connector_id,
                Connector.organization_id == installation.organization_id,
                Connector.status == ConnectorStatus.ACTIVE,
            )
        )
        if connector is None or not _installation_connector_allowed(
            connector, installation.provider
        ):
            raise AuthorizationError("im_delegate_denied", "Installation connector is not active")
        return current

    async def list_installations(
        self,
        session: AsyncSession,
        actor: Principal,
    ) -> list[ImInstallation]:
        await self._authorize(session, actor, "identity.write")
        return list(
            await session.scalars(
                select(ImInstallation)
                .where(ImInstallation.organization_id == actor.organization_id)
                .order_by(ImInstallation.created_at)
            )
        )

    async def create_installation(
        self,
        session: AsyncSession,
        actor: Principal,
        request: CreateImInstallation,
    ) -> ImInstallation:
        current = await self._authorize(session, actor, "identity.write")
        adapter = await load_principal_by_id(
            session, current.organization_id, request.adapter_principal_id
        )
        await self._authorize(session, adapter, "im.delegate")
        connector = await session.scalar(
            select(Connector).where(
                Connector.id == request.connector_id,
                Connector.organization_id == current.organization_id,
                Connector.status == ConnectorStatus.ACTIVE,
            )
        )
        if connector is None or not _installation_connector_allowed(connector, request.provider):
            raise NotFoundError("Active connector", request.connector_id)
        installation = ImInstallation(
            organization_id=current.organization_id,
            created_by=current.id,
            channel=request.provider,
            # The legacy contract has no installation id. Use the vendor app
            # identity as the compatibility-table key without colliding with
            # another corporation that reuses the same app id.
            installation_id=f"{request.external_corp_id}:{request.external_app_id}",
            corp_id=request.external_corp_id,
            app_key=request.external_app_id,
            active=True,
            status="ACTIVE",
            **request.model_dump(),
        )
        try:
            async with session.begin_nested():
                session.add(installation)
                await session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "idempotency_key_reused", "Vendor installation already exists"
            ) from exc
        await self._audit(session, current, "identity.im.installation.create", installation.id)
        return installation

    async def revoke_installation(
        self,
        session: AsyncSession,
        actor: Principal,
        installation_id: UUID,
    ) -> ImInstallation:
        installation = await self._installation(session, actor, installation_id)
        await self._authorize(session, actor, "identity.write", installation.id)
        installation.status = "REVOKED"
        installation.revoked_at = utc_now()
        await session.flush()
        await self._audit(session, actor, "identity.im.installation.revoke", installation.id)
        return installation

    async def bind(
        self,
        session: AsyncSession,
        actor: Principal,
        installation_id: UUID,
        request: CreateInstallationBinding,
    ) -> ImInstallationBinding:
        installation = await self._installation(session, actor, installation_id)
        await self._authorize(session, actor, "identity.write", installation.id)
        if installation.status != "ACTIVE":
            raise AuthorizationError("im_delegate_denied", "Installation is revoked")
        await load_principal_by_id(session, actor.organization_id, request.user_id)
        binding = await session.scalar(
            select(ImInstallationBinding).where(
                ImInstallationBinding.installation_id == installation.id,
                ImInstallationBinding.sender_id == request.sender_id,
            )
        )
        if binding is None:
            binding = ImInstallationBinding(
                organization_id=actor.organization_id,
                installation_id=installation.id,
                sender_id=request.sender_id,
                user_id=request.user_id,
                created_by=actor.id,
            )
            session.add(binding)
        else:
            binding.user_id = request.user_id
            binding.active = True
            binding.revoked_at = None
        await session.flush()
        await self._audit(session, actor, "identity.im.installation.bind", binding.id)
        return binding

    async def revoke_binding(
        self,
        session: AsyncSession,
        actor: Principal,
        installation_id: UUID,
        binding_id: UUID,
    ) -> ImInstallationBinding:
        installation = await self._installation(session, actor, installation_id)
        await self._authorize(session, actor, "identity.write", installation.id)
        binding = await session.scalar(
            select(ImInstallationBinding).where(
                ImInstallationBinding.installation_id == installation.id,
                ImInstallationBinding.id == binding_id,
            )
        )
        if binding is None:
            raise NotFoundError("IM installation binding", binding_id)
        binding.active = False
        binding.revoked_at = utc_now()
        await session.flush()
        await self._audit(session, actor, "identity.im.installation.unbind", binding.id)
        return binding

    async def list_group_audiences(
        self,
        session: AsyncSession,
        actor: Principal,
        installation_id: UUID,
    ) -> list[ImGroupAudience]:
        installation = await self._installation(session, actor, installation_id)
        await self._authorize(session, actor, "identity.write", installation.id)
        return list(
            await session.scalars(
                select(ImGroupAudience)
                .where(
                    ImGroupAudience.organization_id == actor.organization_id,
                    ImGroupAudience.installation_id == installation.id,
                )
                .order_by(ImGroupAudience.conversation_id)
            )
        )

    async def upsert_group_audience(
        self,
        session: AsyncSession,
        actor: Principal,
        installation_id: UUID,
        request: CreateImGroupAudience,
    ) -> ImGroupAudience:
        installation = await self._installation(session, actor, installation_id)
        current = await self._authorize(session, actor, "identity.write", installation.id)
        if installation.status != "ACTIVE":
            raise AuthorizationError("im_delegate_denied", "Installation is revoked")
        workspace = await require_workspace_access(
            session, current, request.workspace_id, write=True
        )
        if workspace.archived_at is not None:
            raise AuthorizationError("im_delegate_denied", "The group workspace is archived")
        member_ids = {str(value) for value in request.member_user_ids}
        workspace_member_ids = set(
            await session.scalars(
                select(WorkspaceMember.user_id).where(
                    WorkspaceMember.organization_id == current.organization_id,
                    WorkspaceMember.workspace_id == workspace.id,
                )
            )
        )
        workspace_member_ids.add(workspace.owner_id)
        if not member_ids.issubset({str(value) for value in workspace_member_ids}):
            raise AuthorizationError(
                "im_delegate_denied",
                "Every configured group member must have current workspace access",
            )
        active_users = set(
            await session.scalars(
                select(User.id).where(
                    User.organization_id == current.organization_id,
                    User.id.in_(request.member_user_ids),
                    User.active.is_(True),
                )
            )
        )
        if active_users != set(request.member_user_ids):
            raise AuthorizationError(
                "im_delegate_denied", "Every configured group member must be active"
            )
        now = utc_now()
        audience = await session.scalar(
            select(ImGroupAudience)
            .where(
                ImGroupAudience.organization_id == current.organization_id,
                ImGroupAudience.installation_id == installation.id,
                ImGroupAudience.conversation_id == request.conversation_id,
            )
            .with_for_update()
        )
        member_user_ids = [str(value) for value in request.member_user_ids]
        verified_until = now + timedelta(minutes=5)
        if audience is None:
            audience = ImGroupAudience(
                organization_id=current.organization_id,
                installation_id=installation.id,
                conversation_id=request.conversation_id,
                workspace_id=workspace.id,
                member_user_ids=member_user_ids,
                member_fingerprint=request.member_fingerprint.lower(),
                max_classification=request.max_classification,
                allow_final_answer=request.allow_final_answer,
                allow_status=request.allow_status,
                status="ACTIVE",
                created_by=current.id,
                verification_source=request.verification_source,
                verified_at=now,
                verified_until=verified_until,
                revoked_at=None,
            )
            session.add(audience)
        else:
            audience.workspace_id = workspace.id
            audience.member_user_ids = member_user_ids
            audience.member_fingerprint = request.member_fingerprint.lower()
            audience.max_classification = request.max_classification
            audience.allow_final_answer = request.allow_final_answer
            audience.allow_status = request.allow_status
            audience.status = "ACTIVE"
            audience.created_by = current.id
            audience.verification_source = request.verification_source
            audience.verified_at = now
            audience.verified_until = verified_until
            audience.revoked_at = None
        await session.flush()
        await self._audit(session, current, "identity.im.group_audience.upsert", audience.id)
        return audience

    async def revoke_group_audience(
        self,
        session: AsyncSession,
        actor: Principal,
        installation_id: UUID,
        audience_id: UUID,
    ) -> ImGroupAudience:
        installation = await self._installation(session, actor, installation_id)
        current = await self._authorize(session, actor, "identity.write", installation.id)
        audience = await session.scalar(
            select(ImGroupAudience)
            .where(
                ImGroupAudience.organization_id == current.organization_id,
                ImGroupAudience.installation_id == installation.id,
                ImGroupAudience.id == audience_id,
            )
            .with_for_update()
        )
        if audience is None:
            raise NotFoundError("IM group audience", audience_id)
        audience.status = "REVOKED"
        audience.revoked_at = utc_now()
        await session.flush()
        await self._audit(session, current, "identity.im.group_audience.revoke", audience.id)
        return audience

    async def _binding(
        self,
        session: AsyncSession,
        installation: ImInstallation,
        sender_id: str,
    ) -> ImInstallationBinding:
        binding = await session.scalar(
            select(ImInstallationBinding)
            .where(
                ImInstallationBinding.organization_id == installation.organization_id,
                ImInstallationBinding.installation_id == installation.id,
                ImInstallationBinding.sender_id == sender_id,
                ImInstallationBinding.active.is_(True),
            )
            .execution_options(populate_existing=True)
        )
        if binding is None:
            raise AuthorizationError("unknown_im_sender", "No active installation-scoped sender")
        return binding

    async def _group_audience(
        self,
        session: AsyncSession,
        installation: ImInstallation,
        conversation_id: str,
    ) -> ImGroupAudience:
        audience = await session.scalar(
            select(ImGroupAudience)
            .where(
                ImGroupAudience.organization_id == installation.organization_id,
                ImGroupAudience.installation_id == installation.id,
                ImGroupAudience.conversation_id == conversation_id,
                ImGroupAudience.status == "ACTIVE",
            )
            .execution_options(populate_existing=True)
        )
        if audience is None:
            raise AuthorizationError(
                "im_delegate_denied", "This group has no active audience authorization"
            )
        if ensure_utc(audience.verified_until) <= utc_now():
            raise AuthorizationError(
                "im_delegate_denied", "The group audience verification has expired"
            )
        return audience

    async def _authorize_group_sender(
        self,
        session: AsyncSession,
        subject: Principal,
        installation: ImInstallation,
        inbox: ImInboxMessage,
    ) -> ImGroupAudience:
        audience = await self._group_audience(session, installation, inbox.conversation_id)
        if str(subject.id) not in {str(value) for value in audience.member_user_ids}:
            raise AuthorizationError(
                "im_delegate_denied", "The sender is not in the verified group audience"
            )
        workspace = await require_workspace_access(
            session, subject, audience.workspace_id, write=True
        )
        if workspace.archived_at is not None:
            raise AuthorizationError("im_delegate_denied", "The group workspace is archived")
        current_members = set(
            await session.scalars(
                select(WorkspaceMember.user_id).where(
                    WorkspaceMember.organization_id == subject.organization_id,
                    WorkspaceMember.workspace_id == workspace.id,
                )
            )
        )
        current_members.add(workspace.owner_id)
        configured_members = {UUID(str(value)) for value in audience.member_user_ids}
        if not configured_members.issubset(current_members):
            raise AuthorizationError(
                "im_delegate_denied", "The verified group audience no longer matches workspace ACL"
            )
        return audience

    async def receive(
        self,
        session: AsyncSession,
        actor: Principal,
        installation_id: UUID,
        message: TrustedImInbound,
    ) -> tuple[ImInboxMessage, bool]:
        installation = await self._installation(session, actor, installation_id)
        actor = await self._adapter(session, actor, installation)
        fingerprint = hashlib.sha256(
            json.dumps(
                message.model_dump(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()
        existing = await session.scalar(
            select(ImInboxMessage).where(
                ImInboxMessage.installation_id == installation.id,
                ImInboxMessage.vendor_event_id == message.vendor_event_id,
            )
        )
        if existing is not None:
            if existing.fingerprint != fingerprint:
                # Return conflict to the API so this audit commits before HTTP 409.
                await self._audit(
                    session, actor, "im.inbox.content_conflict", existing.id, outcome="DENIED"
                )
                return existing, True
            return existing, False
        binding = await self._binding(session, installation, message.sender_id)
        await load_principal_by_id(session, installation.organization_id, binding.user_id)
        inbox = ImInboxMessage(
            organization_id=installation.organization_id,
            installation_id=installation.id,
            fingerprint=fingerprint,
            binding_id=binding.id,
            subject_user_id=binding.user_id,
            **{**message.model_dump(), "text": redact_text(message.text)},
        )
        session.add(inbox)
        await session.flush()
        await self._audit(session, actor, "im.inbox.received", inbox.id)
        return inbox, False

    async def list_messages(
        self,
        session: AsyncSession,
        actor: Principal,
        installation_id: UUID,
        *,
        status: str = "RECEIVED",
        limit: int = 20,
        after: UUID | None = None,
    ) -> list[ImInboxMessage]:
        installation = await self._installation(session, actor, installation_id)
        await self._adapter(session, actor, installation)
        query = select(ImInboxMessage).where(
            ImInboxMessage.organization_id == installation.organization_id,
            ImInboxMessage.installation_id == installation.id,
            ImInboxMessage.status == status,
        )
        if after is not None:
            query = query.where(ImInboxMessage.id > after)
        return list(
            await session.scalars(query.order_by(ImInboxMessage.id).limit(min(max(limit, 1), 100)))
        )

    async def get(
        self,
        session: AsyncSession,
        actor: Principal,
        installation_id: UUID,
        inbox_id: UUID,
    ) -> ImInboxMessage:
        installation = await self._installation(session, actor, installation_id)
        await self._adapter(session, actor, installation)
        return await self._inbox(session, installation, inbox_id)

    async def _inbox(
        self,
        session: AsyncSession,
        installation: ImInstallation,
        inbox_id: UUID,
    ) -> ImInboxMessage:
        inbox = await session.scalar(
            select(ImInboxMessage)
            .where(
                ImInboxMessage.organization_id == installation.organization_id,
                ImInboxMessage.installation_id == installation.id,
                ImInboxMessage.id == inbox_id,
            )
            .execution_options(populate_existing=True)
        )
        if inbox is None:
            raise NotFoundError("IM inbox message", inbox_id)
        return inbox

    async def _conversation_thread(
        self,
        session: AsyncSession,
        subject: Principal,
        installation: ImInstallation,
        inbox: ImInboxMessage,
    ) -> Thread:
        if inbox.conversation_type == "group":
            try:
                audience = await self._authorize_group_sender(session, subject, installation, inbox)
            except AuthorizationError:
                # Admission remains durable for recovery, but an unconfigured or
                # unauthorized group is processed in the sender's private context;
                # the Outbox will never publish that answer back to the group.
                audience = None
            if audience is not None:
                # Group tasks use the operator-bound workspace but get a fresh
                # thread per sender so another member cannot inherit private text.
                return await self.workspaces.create_thread(
                    session,
                    subject,
                    audience.workspace_id,
                    f"im-group:{inbox.conversation_id}:{subject.id}",
                )
        digest = hashlib.sha256(
            json.dumps(
                [
                    inbox.sender_id,
                    inbox.conversation_type,
                    inbox.conversation_id,
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        mapping = await session.scalar(
            select(ImConversationBinding).where(
                ImConversationBinding.organization_id == subject.organization_id,
                ImConversationBinding.installation_id == installation.id,
                ImConversationBinding.subject_user_id == subject.id,
                ImConversationBinding.conversation_digest == digest,
            )
        )
        workspace: Workspace | None = None
        thread: Thread | None = None
        if mapping is not None:
            workspace = await session.scalar(
                select(Workspace)
                .where(
                    Workspace.id == mapping.workspace_id,
                    Workspace.organization_id == subject.organization_id,
                    Workspace.owner_id == subject.id,
                )
                .with_for_update()
            )
            other_member = await session.scalar(
                select(WorkspaceMember.user_id)
                .where(
                    WorkspaceMember.workspace_id == mapping.workspace_id,
                    WorkspaceMember.user_id != subject.id,
                )
                .limit(1)
            )
            if (
                workspace is None
                or workspace.visibility != Visibility.PRIVATE
                or other_member is not None
            ):
                raise AuthorizationError("im_delegate_denied", "IM context is no longer private")
            thread = await session.scalar(
                select(Thread)
                .where(
                    Thread.id == mapping.thread_id,
                    Thread.workspace_id == workspace.id,
                    Thread.organization_id == subject.organization_id,
                )
                .with_for_update()
            )
            if thread is None:
                raise AuthorizationError("im_delegate_denied", "IM context mapping is invalid")
            if workspace.archived_at is not None:
                workspace = None
                thread = None
            elif thread.status != ThreadStatus.ACTIVE:
                thread = None
        if workspace is None:
            workspace = await self.workspaces.create_workspace(
                session,
                subject,
                CreateWorkspaceRequest(
                    name=f"IM:{installation.id}:{subject.id}",
                    description="Installation-scoped IM conversation",
                    visibility=Visibility.PRIVATE,
                ),
            )
        if thread is None:
            thread = await self.workspaces.create_thread(
                session, subject, workspace.id, f"im:{digest}"
            )
        if mapping is None:
            session.add(
                ImConversationBinding(
                    organization_id=subject.organization_id,
                    installation_id=installation.id,
                    subject_user_id=subject.id,
                    conversation_digest=digest,
                    workspace_id=workspace.id,
                    thread_id=thread.id,
                )
            )
        else:
            mapping.workspace_id = workspace.id
            mapping.thread_id = thread.id
        await session.flush()
        return thread

    async def process(
        self,
        session: AsyncSession,
        actor: Principal,
        installation_id: UUID,
        inbox_id: UUID,
    ) -> ImInboxMessage:
        installation = await self._installation(session, actor, installation_id)
        actor = await self._adapter(session, actor, installation)
        inbox = await self._inbox(session, installation, inbox_id)
        binding = await self._binding(session, installation, inbox.sender_id)
        if binding.id != inbox.binding_id or binding.user_id != inbox.subject_user_id:
            raise AuthorizationError("unknown_im_sender", "The admitted sender binding changed")
        subject = await load_principal_by_id(session, installation.organization_id, binding.user_id)
        await self._authorize(session, subject, "workspace.write", installation.id)
        if inbox.status == "PROCESSED":
            return inbox
        inbox.status = "PROCESSING"
        await session.flush()
        thread = await self._conversation_thread(session, subject, installation, inbox)
        turn, run = await self.workspaces.create_turn(
            session,
            subject,
            thread.id,
            CreateTurnRequest(
                input=inbox.text,
                context_refs=[
                    {
                        "type": "im_inbox",
                        "inbox_id": str(inbox.id),
                        "installation_id": str(installation.id),
                        "conversation_type": inbox.conversation_type,
                        "conversation_id": inbox.conversation_id,
                    }
                ],
            ),
        )
        inbox.turn_id = turn.id
        inbox.run_id = run.id
        inbox.status = "PROCESSED"
        inbox.processed_at = utc_now()
        await session.flush()
        await self._audit(session, actor, "im.inbox.processed", inbox.id)
        return inbox
