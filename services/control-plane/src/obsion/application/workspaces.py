from copy import deepcopy
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import CreateTurnRequest, CreateWorkspaceRequest
from obsion.application.conversation_context import ConversationContextService
from obsion.application.run_lifecycle import cancel_active_run_steps
from obsion.application.thread_history import ThreadHistoryResolver
from obsion.common.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.config import Settings
from obsion.db.models import (
    AgentDefinition,
    AgentVersion,
    Artifact,
    Event,
    ModelProfile,
    Run,
    Thread,
    Turn,
    User,
    Workspace,
    WorkspaceMember,
)
from obsion.domain.enums import ActorType, RegistryStatus, RunStatus, ThreadStatus
from obsion.domain.run_state import is_terminal, validate_run_transition
from obsion.model_gateway.workspace_context import snapshot_workspace
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore
from obsion.registry.agent_spec import AgentSpec
from obsion.registry.prompt_pins import names_for_agent_spec, resolve_prompt_pins
from obsion.security.identity import Principal
from obsion.security.redaction import redact_text
from obsion.security.workspace_access import (
    require_run_access,
    require_thread_access,
    require_workspace_access,
    workspace_access_clause,
)
from obsion.telemetry import workspace_context_counter


class WorkspaceService:
    def __init__(
        self,
        settings: Settings,
        event_store: EventStore | None = None,
        audit: AuditWriter | None = None,
    ) -> None:
        self.settings = settings
        self.events = event_store or EventStore()
        self.audit = audit or AuditWriter()
        self.history = ThreadHistoryResolver()
        self.conversation_context = ConversationContextService(settings)

    async def _workspace(
        self,
        session: AsyncSession,
        principal: Principal,
        workspace_id: UUID,
        *,
        write: bool = False,
    ) -> Workspace:
        return await require_workspace_access(session, principal, workspace_id, write=write)

    async def _thread(
        self,
        session: AsyncSession,
        principal: Principal,
        thread_id: UUID,
        *,
        for_update: bool = False,
    ) -> Thread:
        return await require_thread_access(
            session,
            principal,
            thread_id,
            write=for_update,
            for_update=for_update,
        )

    async def create_workspace(
        self,
        session: AsyncSession,
        principal: Principal,
        request: CreateWorkspaceRequest,
    ) -> Workspace:
        workspace = Workspace(
            organization_id=principal.organization_id,
            name=request.name.strip(),
            description=request.description.strip(),
            owner_id=principal.id,
            classification=request.classification,
            visibility=request.visibility,
        )
        session.add(workspace)
        await session.flush()
        correlation_id = new_id()
        await self.events.append(
            session,
            EventDraft(
                name="workspace.created",
                aggregate_type="workspace",
                aggregate_id=workspace.id,
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                payload={"name": workspace.name, "classification": workspace.classification},
            ),
        )
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action="workspace.create",
                resource_type="workspace",
                resource_id=str(workspace.id),
                outcome="SUCCESS",
            ),
        )
        return workspace

    async def list_workspaces(
        self, session: AsyncSession, principal: Principal, include_archived: bool = False
    ) -> list[Workspace]:
        statement = select(Workspace).where(Workspace.organization_id == principal.organization_id)
        statement = statement.where(workspace_access_clause(principal))
        if not include_archived:
            statement = statement.where(Workspace.archived_at.is_(None))
        result = await session.scalars(statement.order_by(Workspace.updated_at.desc()).limit(200))
        return list(result)

    async def create_thread(
        self,
        session: AsyncSession,
        principal: Principal,
        workspace_id: UUID,
        title: str,
        *,
        actor_type: ActorType = ActorType.USER,
    ) -> Thread:
        await self._workspace(session, principal, workspace_id, write=True)
        thread = Thread(
            organization_id=principal.organization_id,
            workspace_id=workspace_id,
            title=title.strip(),
            status=ThreadStatus.ACTIVE,
            created_by=principal.id,
        )
        session.add(thread)
        await session.flush()
        correlation_id = new_id()
        await self.events.append(
            session,
            EventDraft(
                name="thread.created",
                aggregate_type="thread",
                aggregate_id=thread.id,
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=actor_type,
                actor_id=principal.id,
                payload={"workspace_id": str(workspace_id), "title": thread.title},
            ),
        )
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=actor_type,
                actor_id=principal.id,
                action="thread.create",
                resource_type="thread",
                resource_id=str(thread.id),
                outcome="SUCCESS",
                metadata={"workspace_id": str(workspace_id)},
            ),
        )
        return thread

    async def list_threads(
        self,
        session: AsyncSession,
        principal: Principal,
        workspace_id: UUID,
        include_archived: bool = False,
    ) -> list[Thread]:
        await self._workspace(session, principal, workspace_id)
        statement = select(Thread).where(
            Thread.organization_id == principal.organization_id,
            Thread.workspace_id == workspace_id,
        )
        if not include_archived:
            statement = statement.where(Thread.status == ThreadStatus.ACTIVE)
        result = await session.scalars(statement.order_by(Thread.updated_at.desc()).limit(500))
        return list(result)

    async def _effective_turns(
        self,
        session: AsyncSession,
        principal: Principal,
        thread: Thread,
        *,
        lineage: frozenset[UUID] = frozenset(),
    ) -> list[Turn]:
        return await self.history.effective_turns(
            session,
            principal,
            thread,
            lineage=lineage,
        )

    async def add_member(
        self,
        session: AsyncSession,
        principal: Principal,
        workspace_id: UUID,
        user_id: UUID,
        permissions: list[str],
    ) -> WorkspaceMember:
        workspace = await self._workspace(session, principal, workspace_id)
        if workspace.owner_id != principal.id and not principal.can("workspace.manage.all"):
            raise AuthorizationError(
                "workspace_membership_denied", "Workspace membership changes are not permitted"
            )
        normalized = sorted(set(permissions))
        if not normalized or not set(normalized).issubset({"read", "write"}):
            raise ValidationError(
                "workspace_permissions_invalid", "Workspace permissions must be read or write"
            )
        user_exists = await session.scalar(
            select(User.id).where(
                User.id == user_id,
                User.organization_id == principal.organization_id,
                User.active.is_(True),
            )
        )
        if user_exists is None:
            raise NotFoundError("User", user_id)
        member = await session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == user_id,
            )
        )
        if member is None:
            member = WorkspaceMember(
                organization_id=principal.organization_id,
                workspace_id=workspace.id,
                user_id=user_id,
                permissions=normalized,
                can_write="write" in normalized,
                created_by=principal.id,
                created_at=utc_now(),
            )
            session.add(member)
        else:
            member.permissions = normalized
            member.can_write = "write" in normalized
        await self.events.append(
            session,
            EventDraft(
                name="workspace.member_changed",
                aggregate_type="workspace",
                aggregate_id=workspace.id,
                organization_id=principal.organization_id,
                correlation_id=new_id(),
                actor_type=ActorType.USER,
                actor_id=principal.id,
                payload={"user_id": str(user_id), "permissions": normalized},
            ),
        )
        return member

    async def list_members(
        self, session: AsyncSession, principal: Principal, workspace_id: UUID
    ) -> list[WorkspaceMember]:
        await self._workspace(session, principal, workspace_id)
        return list(
            await session.scalars(
                select(WorkspaceMember)
                .where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.organization_id == principal.organization_id,
                )
                .order_by(WorkspaceMember.created_at)
            )
        )

    async def remove_member(
        self,
        session: AsyncSession,
        principal: Principal,
        workspace_id: UUID,
        user_id: UUID,
    ) -> None:
        workspace = await self._workspace(session, principal, workspace_id)
        if workspace.owner_id != principal.id and not principal.can("workspace.manage.all"):
            raise AuthorizationError(
                "workspace_membership_denied", "Workspace membership changes are not permitted"
            )
        member = await session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.organization_id == principal.organization_id,
                WorkspaceMember.user_id == user_id,
            )
        )
        if member is None:
            raise NotFoundError("Workspace member", user_id)
        await session.delete(member)
        await self.events.append(
            session,
            EventDraft(
                name="workspace.member_removed",
                aggregate_type="workspace",
                aggregate_id=workspace.id,
                organization_id=principal.organization_id,
                correlation_id=new_id(),
                actor_type=ActorType.USER,
                actor_id=principal.id,
                payload={"user_id": str(user_id)},
            ),
        )

    async def archive_thread(
        self, session: AsyncSession, principal: Principal, thread_id: UUID
    ) -> Thread:
        thread = await self._thread(session, principal, thread_id, for_update=True)
        if thread.status == ThreadStatus.ARCHIVED:
            return thread
        active_run_id = await session.scalar(
            select(Run.id)
            .join(Turn, Turn.id == Run.turn_id)
            .where(
                Turn.thread_id == thread.id,
                Turn.organization_id == principal.organization_id,
                Run.organization_id == principal.organization_id,
                Run.status.not_in([RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED]),
            )
            .limit(1)
        )
        if active_run_id is not None:
            raise ConflictError(
                "thread_has_active_run",
                "A thread with an active run must finish or be cancelled before archiving",
            )
        thread.status = ThreadStatus.ARCHIVED
        thread.archived_at = utc_now()
        correlation_id = new_id()
        await self.events.append(
            session,
            EventDraft(
                name="thread.archived",
                aggregate_type="thread",
                aggregate_id=thread.id,
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                payload={},
            ),
        )
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action="thread.archive",
                resource_type="thread",
                resource_id=str(thread.id),
                outcome="SUCCESS",
            ),
        )
        return thread

    async def resume_thread(
        self, session: AsyncSession, principal: Principal, thread_id: UUID
    ) -> Thread:
        thread = await self._thread(session, principal, thread_id, for_update=True)
        if thread.status == ThreadStatus.ACTIVE:
            return thread
        thread.status = ThreadStatus.ACTIVE
        thread.archived_at = None
        correlation_id = new_id()
        await self.events.append(
            session,
            EventDraft(
                name="thread.resumed",
                aggregate_type="thread",
                aggregate_id=thread.id,
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                payload={},
            ),
        )
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action="thread.resume",
                resource_type="thread",
                resource_id=str(thread.id),
                outcome="SUCCESS",
            ),
        )
        return thread

    async def fork_thread(
        self,
        session: AsyncSession,
        principal: Principal,
        thread_id: UUID,
        from_turn_id: UUID | None,
        title: str | None,
    ) -> Thread:
        parent = await self._thread(session, principal, thread_id, for_update=True)
        parent_turns = await self._effective_turns(session, principal, parent)
        resolved_fork_turn_id = from_turn_id or (parent_turns[-1].id if parent_turns else None)
        if resolved_fork_turn_id is not None and not any(
            item.id == resolved_fork_turn_id for item in parent_turns
        ):
            raise NotFoundError("Turn", resolved_fork_turn_id)
        thread = Thread(
            organization_id=principal.organization_id,
            workspace_id=parent.workspace_id,
            title=(title or f"{parent.title} (fork)").strip(),
            status=ThreadStatus.ACTIVE,
            created_by=principal.id,
            parent_thread_id=parent.id,
            forked_from_turn_id=resolved_fork_turn_id,
        )
        session.add(thread)
        await session.flush()
        correlation_id = new_id()
        if parent.status == ThreadStatus.ACTIVE:
            parent.status = ThreadStatus.ARCHIVED
            parent.archived_at = utc_now()
            await self.events.append(
                session,
                EventDraft(
                    name="thread.archived",
                    aggregate_type="thread",
                    aggregate_id=parent.id,
                    organization_id=principal.organization_id,
                    correlation_id=correlation_id,
                    actor_type=ActorType.USER,
                    actor_id=principal.id,
                    payload={},
                ),
            )
            await self.audit.write(
                session,
                AuditDraft(
                    organization_id=principal.organization_id,
                    correlation_id=correlation_id,
                    actor_type=ActorType.USER,
                    actor_id=principal.id,
                    action="thread.archive",
                    resource_type="thread",
                    resource_id=str(parent.id),
                    outcome="SUCCESS",
                    metadata={"reason": "fork", "fork_thread_id": str(thread.id)},
                ),
            )
        await self.events.append(
            session,
            EventDraft(
                name="thread.forked",
                aggregate_type="thread",
                aggregate_id=thread.id,
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                payload={
                    "parent_thread_id": str(parent.id),
                    "forked_from_turn_id": (
                        str(resolved_fork_turn_id) if resolved_fork_turn_id else None
                    ),
                },
            ),
        )
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action="thread.fork",
                resource_type="thread",
                resource_id=str(thread.id),
                outcome="SUCCESS",
                metadata={
                    "parent_thread_id": str(parent.id),
                    "forked_from_turn_id": (
                        str(resolved_fork_turn_id) if resolved_fork_turn_id else None
                    ),
                },
            ),
        )
        return thread

    async def list_thread_events(
        self,
        session: AsyncSession,
        principal: Principal,
        thread_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> list[Event]:
        await self._thread(session, principal, thread_id)
        return await self.events.list_aggregate(
            session,
            principal.organization_id,
            "thread",
            thread_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def create_turn(
        self,
        session: AsyncSession,
        principal: Principal,
        thread_id: UUID,
        request: CreateTurnRequest,
        *,
        actor_type: ActorType = ActorType.USER,
    ) -> tuple[Turn, Run]:
        thread = await self._thread(session, principal, thread_id, for_update=True)
        if thread.status != ThreadStatus.ACTIVE:
            raise ConflictError("thread_archived", "An archived thread must be resumed first")
        effective_turns = await self._effective_turns(session, principal, thread)
        ordinal = (effective_turns[-1].ordinal if effective_turns else 0) + 1
        now = utc_now()
        agent_version = await session.scalar(
            select(AgentVersion)
            .join(AgentDefinition, AgentDefinition.id == AgentVersion.agent_id)
            .where(
                AgentDefinition.organization_id == principal.organization_id,
                AgentDefinition.name == "general-agent",
                AgentDefinition.status == RegistryStatus.ACTIVE,
                AgentDefinition.active_version == AgentVersion.version,
            )
            .limit(1)
        )
        if agent_version is None:
            raise NotFoundError("Active GeneralAgent", "general-agent")
        agent_spec = AgentSpec.from_dict(agent_version.spec, source="GeneralAgent")
        timeout_seconds = min(self.settings.run_timeout_seconds, agent_spec.timeout_seconds)
        profile_name = request.model_profile or agent_spec.model_profile
        model_profile = await session.scalar(
            select(ModelProfile).where(
                ModelProfile.organization_id == principal.organization_id,
                ModelProfile.name == profile_name,
                ModelProfile.enabled.is_(True),
            )
        )
        if model_profile is None:
            raise NotFoundError("Model profile", profile_name)
        prompt_pins = await resolve_prompt_pins(
            session,
            principal.organization_id,
            names_for_agent_spec(agent_version.spec),
        )
        workspace = await session.scalar(
            select(Workspace).where(
                Workspace.id == thread.workspace_id,
                Workspace.organization_id == principal.organization_id,
            )
        )
        if workspace is None:
            raise NotFoundError("Workspace", thread.workspace_id)
        workspace_context = snapshot_workspace(
            workspace_id=workspace.id,
            name=workspace.name,
            classification=workspace.classification.value,
            visibility=workspace.visibility.value,
            description=workspace.description,
        )
        workspace_context_counter.add(
            1,
            {"has_description": str(bool(workspace_context["description"].strip())).lower()},
        )
        sanitized_input = redact_text(request.input)
        turn = Turn(
            organization_id=principal.organization_id,
            thread_id=thread.id,
            ordinal=ordinal,
            created_by=principal.id,
            # The raw request never enters durable storage.  Both legacy fields
            # remain populated for API compatibility, but carry the same safe text.
            input_text=sanitized_input,
            sanitized_input=sanitized_input,
            context_refs=request.context_refs,
            attachment_refs=await self._validate_attachments(
                session,
                principal,
                thread.workspace_id,
                request.attachment_refs,
            ),
            created_at=now,
        )
        session.add(turn)
        await session.flush()
        run = Run(
            organization_id=principal.organization_id,
            turn_id=turn.id,
            status=RunStatus.PENDING,
            agent_version_id=agent_version.id,
            model_profile_id=model_profile.id,
            prompt_pins=prompt_pins,
            workspace_context=workspace_context,
            max_steps=min(self.settings.run_max_steps, agent_spec.max_steps),
            timeout_seconds=timeout_seconds,
            max_input_tokens=self.settings.run_max_input_tokens,
            max_output_tokens=self.settings.run_max_output_tokens,
            max_cost_amount=self.settings.run_max_cost_amount,
            deadline_at=now + timedelta(seconds=timeout_seconds),
        )
        session.add(run)
        await session.flush()
        conversation_snapshots = await self.conversation_context.capture(
            session,
            principal,
            run,
            turn,
            thread,
            effective_turns,
        )
        correlation_id = run.id
        await self.events.append(
            session,
            EventDraft(
                name="turn.created",
                aggregate_type="run",
                aggregate_id=run.id,
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=actor_type,
                actor_id=principal.id,
                run_id=run.id,
                payload={
                    "turn_id": str(turn.id),
                    "thread_id": str(thread.id),
                    "ordinal": ordinal,
                    "input": turn.sanitized_input,
                },
            ),
        )
        await self.events.append(
            session,
            EventDraft(
                name="run.created",
                aggregate_type="run",
                aggregate_id=run.id,
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                run_id=run.id,
                payload={
                    "turn_id": str(turn.id),
                    "status": run.status,
                    "conversation_snapshot_count": len(conversation_snapshots),
                },
            ),
        )
        return turn, run

    async def _validate_attachments(
        self,
        session: AsyncSession,
        principal: Principal,
        workspace_id: UUID,
        references: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for reference in references:
            if reference.get("type") != "artifact":
                raise ValidationError(
                    "attachment_type_unsupported",
                    "Turn attachments must reference a workspace artifact",
                )
            try:
                artifact_id = UUID(str(reference.get("artifact_id", "")))
            except ValueError as exc:
                raise ValidationError(
                    "attachment_reference_invalid", "Attachment artifact ID is invalid"
                ) from exc
            artifact = await session.scalar(
                select(Artifact).where(
                    Artifact.id == artifact_id,
                    Artifact.organization_id == principal.organization_id,
                    Artifact.workspace_id == workspace_id,
                )
            )
            if artifact is None:
                raise NotFoundError("Artifact", artifact_id)
            normalized.append(
                {
                    "type": "artifact",
                    "artifact_id": str(artifact.id),
                    "title": artifact.title,
                    "media_type": artifact.media_type,
                    "classification": artifact.classification,
                }
            )
        return normalized

    async def list_turns(
        self, session: AsyncSession, principal: Principal, thread_id: UUID
    ) -> list[Turn]:
        thread = await self._thread(session, principal, thread_id)
        return await self._effective_turns(session, principal, thread)

    async def list_thread_runs(
        self, session: AsyncSession, principal: Principal, thread_id: UUID
    ) -> list[Run]:
        thread = await self._thread(session, principal, thread_id)
        turns = await self._effective_turns(session, principal, thread)
        if not turns:
            return []
        runs = list(
            await session.scalars(
                select(Run)
                .where(
                    Run.organization_id == principal.organization_id,
                    Run.turn_id.in_([item.id for item in turns]),
                )
                .order_by(Run.created_at)
            )
        )
        runs_by_turn: dict[UUID, list[Run]] = {}
        for run in runs:
            runs_by_turn.setdefault(run.turn_id, []).append(run)
        return [run for turn in turns for run in runs_by_turn.get(turn.id, [])]

    async def get_run(self, session: AsyncSession, principal: Principal, run_id: UUID) -> Run:
        return await require_run_access(session, principal, run_id)

    async def cancel_run(self, session: AsyncSession, principal: Principal, run_id: UUID) -> Run:
        run = await require_run_access(session, principal, run_id, write=True, for_update=True)
        if is_terminal(run.status):
            return run
        now = utc_now()
        previous_status = run.status
        run.cancellation_requested_at = now
        validate_run_transition(run.status, RunStatus.CANCELLED)
        run.status = RunStatus.CANCELLED
        run.completed_at = now
        run.lease_owner = None
        run.lease_expires_at = None
        cancelled_steps = await cancel_active_run_steps(
            session,
            principal.organization_id,
            run.id,
            completed_at=now,
        )
        await self.events.append(
            session,
            EventDraft(
                name="run.cancellation_requested",
                aggregate_type="run",
                aggregate_id=run.id,
                organization_id=principal.organization_id,
                correlation_id=run.id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                run_id=run.id,
                payload={"status": previous_status},
            ),
        )
        await self.events.append(
            session,
            EventDraft(
                name="run.cancelled",
                aggregate_type="run",
                aggregate_id=run.id,
                organization_id=principal.organization_id,
                correlation_id=run.id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                run_id=run.id,
                payload={},
            ),
        )
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=run.id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action="run.cancel",
                resource_type="run",
                resource_id=str(run.id),
                outcome="SUCCESS",
                metadata={
                    "previous_status": previous_status,
                    "cancelled_steps": cancelled_steps,
                },
            ),
        )
        return run

    async def replay_run(self, session: AsyncSession, principal: Principal, run_id: UUID) -> Run:
        source = await require_run_access(session, principal, run_id, write=True)
        if not is_terminal(source.status):
            raise ConflictError(
                "run_not_replayable",
                "Only a terminal run has a stable replay snapshot",
                status=source.status,
            )
        now = utc_now()
        replay = Run(
            organization_id=principal.organization_id,
            turn_id=source.turn_id,
            status=RunStatus.PENDING,
            agent_version_id=source.agent_version_id,
            model_profile_id=source.model_profile_id,
            prompt_pins=list(source.prompt_pins or []),
            context_budget=deepcopy(source.context_budget or {}),
            conversation_compact=deepcopy(source.conversation_compact or {}),
            workspace_context=deepcopy(source.workspace_context or {}),
            max_steps=source.max_steps,
            timeout_seconds=source.timeout_seconds,
            max_input_tokens=source.max_input_tokens,
            max_output_tokens=source.max_output_tokens,
            max_cost_amount=source.max_cost_amount,
            deadline_at=now + timedelta(seconds=source.timeout_seconds),
            replay_of_run_id=source.id,
        )
        session.add(replay)
        await session.flush()
        await self.events.append(
            session,
            EventDraft(
                name="run.replay_requested",
                aggregate_type="run",
                aggregate_id=replay.id,
                organization_id=principal.organization_id,
                correlation_id=replay.id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                run_id=replay.id,
                payload={"source_run_id": str(source.id)},
            ),
        )
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=replay.id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action="run.replay.request",
                resource_type="run",
                resource_id=str(replay.id),
                outcome="QUEUED",
                metadata={"source_run_id": str(source.id), "source_status": source.status},
            ),
        )
        return replay
