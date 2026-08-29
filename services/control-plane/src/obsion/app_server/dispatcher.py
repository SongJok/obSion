from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from obsion.api.schemas import CreateTurnRequest
from obsion.app_server.protocol import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    JsonRpcRequest,
    ProtocolFailure,
    error_response,
    success_response,
)
from obsion.app_server.schemas import (
    ApprovalDecideParams,
    ApprovalListParams,
    ArtifactGetParams,
    ArtifactListParams,
    RunEventsParams,
    RunMutationParams,
    RunReadParams,
    ThreadCreateParams,
    ThreadEventsParams,
    ThreadForkParams,
    ThreadListParams,
    ThreadMutationParams,
    ThreadReadParams,
    TurnCreateParams,
    WorkspaceListParams,
)
from obsion.application.app_server import AppServerApplication, RecordedAppServerError
from obsion.common.error_mapping import application_error_code
from obsion.common.errors import ObsionError
from obsion.security.identity import Principal

JsonResult = dict[str, Any] | list[dict[str, Any]]
ParamsT = TypeVar("ParamsT", bound=BaseModel)


class AppServerDispatcher:
    """Validate JSON-RPC params and delegate every use case to the application layer."""

    def __init__(self, application: AppServerApplication) -> None:
        self.application = application

    async def dispatch(
        self,
        request: JsonRpcRequest,
        principal: Principal,
        correlation_id: UUID,
    ) -> dict[str, Any] | None:
        if not request.has_id:
            return None
        assert request.request_id is not None
        try:
            result = await self._invoke(request, principal, correlation_id)
        except PydanticValidationError as exc:
            return error_response(
                request.request_id,
                INVALID_PARAMS,
                "Invalid method params",
                {"issues": exc.errors(include_input=False, include_url=False)},
            )
        except ProtocolFailure as exc:
            return error_response(request.request_id, exc.code, exc.message, exc.data)
        except RecordedAppServerError as exc:
            return {"jsonrpc": "2.0", "id": request.request_id, "error": exc.error}
        except ObsionError as exc:
            return self._domain_error_response(request.request_id, exc, correlation_id)
        return success_response(request.request_id, result)

    async def _invoke(
        self,
        request: JsonRpcRequest,
        principal: Principal,
        correlation_id: UUID,
    ) -> JsonResult:
        method = request.method
        if method == "workspace.list":
            workspace_list = self._validate(WorkspaceListParams, request.params)
            return await self.application.list_workspaces(
                principal, include_archived=workspace_list.include_archived
            )
        if method == "thread.list":
            thread_list = self._validate(ThreadListParams, request.params)
            return await self.application.list_threads(
                principal,
                thread_list.workspace_id,
                include_archived=thread_list.include_archived,
            )
        if method == "thread.create":
            thread_create = self._validate(ThreadCreateParams, request.params)
            return await self.application.create_thread(
                principal,
                correlation_id,
                client_request_id=thread_create.client_request_id,
                workspace_id=thread_create.workspace_id,
                title=thread_create.title,
                fingerprint_params=self._fingerprint_params(thread_create),
            )
        if method == "thread.archive":
            thread_archive = self._validate(ThreadMutationParams, request.params)
            return await self.application.archive_thread(
                principal,
                correlation_id,
                client_request_id=thread_archive.client_request_id,
                thread_id=thread_archive.thread_id,
                fingerprint_params=self._fingerprint_params(thread_archive),
            )
        if method == "thread.resume":
            thread_resume = self._validate(ThreadMutationParams, request.params)
            return await self.application.resume_thread(
                principal,
                correlation_id,
                client_request_id=thread_resume.client_request_id,
                thread_id=thread_resume.thread_id,
                fingerprint_params=self._fingerprint_params(thread_resume),
            )
        if method == "thread.fork":
            thread_fork = self._validate(ThreadForkParams, request.params)
            return await self.application.fork_thread(
                principal,
                correlation_id,
                client_request_id=thread_fork.client_request_id,
                thread_id=thread_fork.thread_id,
                from_turn_id=thread_fork.from_turn_id,
                title=thread_fork.title,
                fingerprint_params=self._fingerprint_params(thread_fork),
            )
        if method == "thread.turns":
            thread_turns = self._validate(ThreadReadParams, request.params)
            return await self.application.list_turns(principal, thread_turns.thread_id)
        if method == "thread.runs":
            thread_runs = self._validate(ThreadReadParams, request.params)
            return await self.application.list_thread_runs(principal, thread_runs.thread_id)
        if method == "thread.events":
            thread_events = self._validate(ThreadEventsParams, request.params)
            return await self.application.list_thread_events(
                principal,
                thread_events.thread_id,
                after_sequence=thread_events.after_sequence,
                limit=thread_events.limit,
            )
        if method == "turn.create":
            turn_create = self._validate(TurnCreateParams, request.params)
            turn_request = CreateTurnRequest.model_validate(
                turn_create.model_dump(exclude={"client_request_id", "thread_id"})
            )
            return await self.application.create_turn(
                principal,
                correlation_id,
                client_request_id=turn_create.client_request_id,
                thread_id=turn_create.thread_id,
                request=turn_request,
                fingerprint_params=self._fingerprint_params(turn_create),
            )
        if method == "run.get":
            run_read = self._validate(RunReadParams, request.params)
            return await self.application.get_run(principal, run_read.run_id)
        if method == "run.cancel":
            run_cancel = self._validate(RunMutationParams, request.params)
            return await self.application.cancel_run(
                principal,
                correlation_id,
                client_request_id=run_cancel.client_request_id,
                run_id=run_cancel.run_id,
                fingerprint_params=self._fingerprint_params(run_cancel),
            )
        if method == "run.replay":
            run_replay = self._validate(RunMutationParams, request.params)
            return await self.application.replay_run(
                principal,
                correlation_id,
                client_request_id=run_replay.client_request_id,
                run_id=run_replay.run_id,
                fingerprint_params=self._fingerprint_params(run_replay),
            )
        if method == "run.events":
            run_events = self._validate(RunEventsParams, request.params)
            return await self.application.list_run_events(
                principal,
                run_events.run_id,
                after_sequence=run_events.after_sequence,
                limit=run_events.limit,
            )
        if method == "approval.list":
            approval_list = self._validate(ApprovalListParams, request.params)
            return await self.application.list_approvals(principal, approval_list.status)
        if method == "approval.decide":
            approval_decide = self._validate(ApprovalDecideParams, request.params)
            return await self.application.decide_approval(
                principal,
                correlation_id,
                client_request_id=approval_decide.client_request_id,
                approval_id=approval_decide.approval_id,
                approve=approval_decide.decision == "approve",
                reason=approval_decide.reason,
                fingerprint_params=self._fingerprint_params(approval_decide),
            )
        if method == "artifact.list":
            artifact_list = self._validate(ArtifactListParams, request.params)
            return await self.application.list_artifacts(principal, artifact_list.workspace_id)
        if method == "artifact.get":
            artifact_get = self._validate(ArtifactGetParams, request.params)
            return await self.application.get_artifact(principal, artifact_get.artifact_id)
        raise ProtocolFailure(
            METHOD_NOT_FOUND,
            "Method not found",
            data={"method": method},
        )

    @staticmethod
    def _validate(model: type[ParamsT], params: dict[str, Any]) -> ParamsT:
        return model.model_validate(params)

    @staticmethod
    def _fingerprint_params(params: BaseModel) -> dict[str, Any]:
        dumped = params.model_dump(mode="json")
        dumped.pop("client_request_id", None)
        return dumped

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

    @classmethod
    def _domain_error_response(
        cls,
        request_id: str | int,
        exc: ObsionError,
        correlation_id: UUID,
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": cls._domain_error(exc, correlation_id),
        }
