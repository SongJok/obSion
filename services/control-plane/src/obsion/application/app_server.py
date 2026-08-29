from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import (
    ApprovalView,
    ArtifactView,
    CreateTurnRequest,
    EventView,
    RunView,
    ThreadView,
    TurnCreatedView,
    TurnView,
    WorkspaceView,
)
from obsion.application.approvals import ApprovalService
from obsion.application.workspaces import WorkspaceService
from obsion.artifacts.service import ArtifactService
from obsion.common.error_mapping import application_error_code
from obsion.common.errors import ObsionError
from obsion.config import Settings
from obsion.db.session import Database
from obsion.domain.enums import ApprovalStatus, RunStatus
from obsion.persistence.app_server_requests import AppServerRequestStore, params_fingerprint
from obsion.persistence.events import EventStore
from obsion.security.auth import authenticate_principal, authenticate_session_principal
from obsion.security.identity import Principal
from obsion.security.workspace_access import require_run_access

JsonResult = dict[str, Any] | list[dict[str, Any]]
MutationHandler = Callable[[AsyncSession], Awaitable[JsonResult]]


@dataclass(frozen=True, slots=True)
class RecordedAppServerError(Exception):
    error: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RunEventBatch:
    status: RunStatus
    events: list[dict[str, Any]]


class AppServerApplication:
    """Application boundary used by the JSON-RPC adapter.

    This class owns database sessions and transactions so the App Server transport
    remains a protocol adapter and cannot query persistence or invoke a model directly.
    """

    def __init__(
        self,
        database: Database,
        settings: Settings,
        workspace_service: WorkspaceService,
        artifact_service: ArtifactService,
    ) -> None:
        self.database = database
        self.settings = settings
        self.workspaces = workspace_service
        self.approvals = ApprovalService()
        self.artifacts = artifact_service
        self.events = EventStore()
        self.requests = AppServerRequestStore()

    async def authenticate(self, bearer_token: str | None) -> Principal:
        async with self.database.sessions() as session:
            return await authenticate_principal(session, self.settings, bearer_token)

    async def authenticate_session(self, session_token: str | None) -> Principal:
        async with self.database.sessions() as session:
            return await authenticate_session_principal(session, session_token)

    async def list_workspaces(self, principal: Principal, *, include_archived: bool) -> JsonResult:
        async with self.database.sessions() as session:
            workspaces = await self.workspaces.list_workspaces(session, principal, include_archived)
        return [WorkspaceView.model_validate(item).model_dump(mode="json") for item in workspaces]

    async def list_threads(
        self,
        principal: Principal,
        workspace_id: UUID,
        *,
        include_archived: bool,
    ) -> JsonResult:
        async with self.database.sessions() as session:
            threads = await self.workspaces.list_threads(
                session,
                principal,
                workspace_id,
                include_archived,
            )
        return [ThreadView.model_validate(item).model_dump(mode="json") for item in threads]

    async def create_thread(
        self,
        principal: Principal,
        correlation_id: UUID,
        *,
        client_request_id: str,
        workspace_id: UUID,
        title: str,
        fingerprint_params: dict[str, Any],
    ) -> JsonResult:
        async def operation(session: AsyncSession) -> JsonResult:
            thread = await self.workspaces.create_thread(session, principal, workspace_id, title)
            return ThreadView.model_validate(thread).model_dump(mode="json")

        return await self._mutate(
            "thread.create",
            principal,
            correlation_id,
            client_request_id,
            fingerprint_params,
            operation,
        )

    async def archive_thread(
        self,
        principal: Principal,
        correlation_id: UUID,
        *,
        client_request_id: str,
        thread_id: UUID,
        fingerprint_params: dict[str, Any],
    ) -> JsonResult:
        async def operation(session: AsyncSession) -> JsonResult:
            thread = await self.workspaces.archive_thread(session, principal, thread_id)
            return ThreadView.model_validate(thread).model_dump(mode="json")

        return await self._mutate(
            "thread.archive",
            principal,
            correlation_id,
            client_request_id,
            fingerprint_params,
            operation,
        )

    async def resume_thread(
        self,
        principal: Principal,
        correlation_id: UUID,
        *,
        client_request_id: str,
        thread_id: UUID,
        fingerprint_params: dict[str, Any],
    ) -> JsonResult:
        async def operation(session: AsyncSession) -> JsonResult:
            thread = await self.workspaces.resume_thread(session, principal, thread_id)
            return ThreadView.model_validate(thread).model_dump(mode="json")

        return await self._mutate(
            "thread.resume",
            principal,
            correlation_id,
            client_request_id,
            fingerprint_params,
            operation,
        )

    async def fork_thread(
        self,
        principal: Principal,
        correlation_id: UUID,
        *,
        client_request_id: str,
        thread_id: UUID,
        from_turn_id: UUID | None,
        title: str | None,
        fingerprint_params: dict[str, Any],
    ) -> JsonResult:
        async def operation(session: AsyncSession) -> JsonResult:
            thread = await self.workspaces.fork_thread(
                session,
                principal,
                thread_id,
                from_turn_id,
                title,
            )
            return ThreadView.model_validate(thread).model_dump(mode="json")

        return await self._mutate(
            "thread.fork",
            principal,
            correlation_id,
            client_request_id,
            fingerprint_params,
            operation,
        )

    async def list_turns(self, principal: Principal, thread_id: UUID) -> JsonResult:
        async with self.database.sessions() as session:
            turns = await self.workspaces.list_turns(session, principal, thread_id)
        return [TurnView.model_validate(item).model_dump(mode="json") for item in turns]

    async def list_thread_runs(self, principal: Principal, thread_id: UUID) -> JsonResult:
        async with self.database.sessions() as session:
            runs = await self.workspaces.list_thread_runs(session, principal, thread_id)
        return [RunView.model_validate(item).model_dump(mode="json") for item in runs]

    async def list_thread_events(
        self,
        principal: Principal,
        thread_id: UUID,
        *,
        after_sequence: int,
        limit: int,
    ) -> JsonResult:
        async with self.database.sessions() as session:
            events = await self.workspaces.list_thread_events(
                session,
                principal,
                thread_id,
                after_sequence=after_sequence,
                limit=limit,
            )
        return [EventView.model_validate(item).model_dump(mode="json") for item in events]

    async def create_turn(
        self,
        principal: Principal,
        correlation_id: UUID,
        *,
        client_request_id: str,
        thread_id: UUID,
        request: CreateTurnRequest,
        fingerprint_params: dict[str, Any],
    ) -> JsonResult:
        async def operation(session: AsyncSession) -> JsonResult:
            turn, run = await self.workspaces.create_turn(session, principal, thread_id, request)
            return TurnCreatedView(
                turn=TurnView.model_validate(turn),
                run=RunView.model_validate(run),
            ).model_dump(mode="json")

        return await self._mutate(
            "turn.create",
            principal,
            correlation_id,
            client_request_id,
            fingerprint_params,
            operation,
        )

    async def get_run(self, principal: Principal, run_id: UUID) -> JsonResult:
        async with self.database.sessions() as session:
            run = await self.workspaces.get_run(session, principal, run_id)
        return RunView.model_validate(run).model_dump(mode="json")

    async def cancel_run(
        self,
        principal: Principal,
        correlation_id: UUID,
        *,
        client_request_id: str,
        run_id: UUID,
        fingerprint_params: dict[str, Any],
    ) -> JsonResult:
        async def operation(session: AsyncSession) -> JsonResult:
            run = await self.workspaces.cancel_run(session, principal, run_id)
            return RunView.model_validate(run).model_dump(mode="json")

        return await self._mutate(
            "run.cancel",
            principal,
            correlation_id,
            client_request_id,
            fingerprint_params,
            operation,
        )

    async def replay_run(
        self,
        principal: Principal,
        correlation_id: UUID,
        *,
        client_request_id: str,
        run_id: UUID,
        fingerprint_params: dict[str, Any],
    ) -> JsonResult:
        async def operation(session: AsyncSession) -> JsonResult:
            run = await self.workspaces.replay_run(session, principal, run_id)
            return RunView.model_validate(run).model_dump(mode="json")

        return await self._mutate(
            "run.replay",
            principal,
            correlation_id,
            client_request_id,
            fingerprint_params,
            operation,
        )

    async def list_run_events(
        self,
        principal: Principal,
        run_id: UUID,
        *,
        after_sequence: int,
        limit: int,
    ) -> JsonResult:
        batch = await self.run_event_batch(
            principal,
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )
        return batch.events

    async def run_status(self, principal: Principal, run_id: UUID) -> RunStatus:
        async with self.database.sessions() as session:
            run = await require_run_access(session, principal, run_id)
        return run.status

    async def run_event_batch(
        self,
        principal: Principal,
        run_id: UUID,
        *,
        after_sequence: int,
        limit: int,
    ) -> RunEventBatch:
        async with self.database.sessions() as session:
            run = await require_run_access(session, principal, run_id)
            events = await self.events.list_run(
                session,
                principal.organization_id,
                run_id,
                after_sequence=after_sequence,
                limit=limit,
            )
        return RunEventBatch(
            status=run.status,
            events=[EventView.model_validate(item).model_dump(mode="json") for item in events],
        )

    async def list_approvals(
        self, principal: Principal, status: ApprovalStatus | None
    ) -> JsonResult:
        async with self.database.sessions() as session:
            approvals = await self.approvals.list(session, principal, status)
        return [ApprovalView.model_validate(item).model_dump(mode="json") for item in approvals]

    async def decide_approval(
        self,
        principal: Principal,
        correlation_id: UUID,
        *,
        client_request_id: str,
        approval_id: UUID,
        approve: bool,
        reason: str,
        fingerprint_params: dict[str, Any],
    ) -> JsonResult:
        async def operation(session: AsyncSession) -> JsonResult:
            approval = await self.approvals.decide(
                session,
                principal,
                approval_id,
                approve=approve,
                reason=reason,
            )
            return ApprovalView.model_validate(approval).model_dump(mode="json")

        return await self._mutate(
            "approval.decide",
            principal,
            correlation_id,
            client_request_id,
            fingerprint_params,
            operation,
        )

    async def list_artifacts(self, principal: Principal, workspace_id: UUID) -> JsonResult:
        async with self.database.sessions() as session:
            artifacts = await self.artifacts.list_workspace(session, principal, workspace_id)
        return [ArtifactView.model_validate(item).model_dump(mode="json") for item in artifacts]

    async def get_artifact(self, principal: Principal, artifact_id: UUID) -> JsonResult:
        async with self.database.sessions() as session:
            artifact = await self.artifacts.get_metadata(session, principal, artifact_id)
        return ArtifactView.model_validate(artifact).model_dump(mode="json")

    async def _mutate(
        self,
        method: str,
        principal: Principal,
        correlation_id: UUID,
        client_request_id: str,
        fingerprint_params: dict[str, Any],
        handler: MutationHandler,
    ) -> JsonResult:
        outcome: dict[str, Any]
        async with self.database.sessions() as session, session.begin():
            claim = await self.requests.claim(
                session,
                principal,
                client_request_id=client_request_id,
                method=method,
                fingerprint=params_fingerprint(fingerprint_params),
                retention_hours=self.settings.app_server_idempotency_retention_hours,
            )
            if claim.replayed_response is not None:
                outcome = claim.replayed_response
            else:
                try:
                    result = await handler(session)
                    outcome = {"result": result}
                except ObsionError as exc:
                    outcome = {"error": self._domain_error(exc, correlation_id)}
                await self.requests.complete(session, claim.record, outcome)
        if error := outcome.get("error"):
            assert isinstance(error, dict)
            raise RecordedAppServerError(error)
        stored_result = outcome.get("result")
        assert isinstance(stored_result, (dict, list))
        return stored_result

    @staticmethod
    def _domain_error(exc: ObsionError, correlation_id: UUID) -> dict[str, Any]:
        return {
            "code": application_error_code(exc.status_code),
            "message": exc.message,
            "data": {
                "code": exc.code,
                "status": exc.status_code,
                "correlation_id": str(correlation_id),
                "details": exc.details,
            },
        }
