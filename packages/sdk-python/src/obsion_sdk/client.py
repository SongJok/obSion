import json
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx


class ObsionAPIError(Exception):
    def __init__(self, status_code: int, code: str, message: str, correlation_id: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.correlation_id = correlation_id


class AsyncObsionClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self) -> "AsyncObsionClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def create_workspace(
        self, name: str, *, description: str = "", visibility: str = "PRIVATE"
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                "/api/v1/workspaces",
                json={"name": name, "description": description, "visibility": visibility},
            ),
        )

    async def list_workspaces(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request(
                "GET", "/api/v1/workspaces", params={"include_archived": include_archived}
            ),
        )

    async def create_thread(self, workspace_id: str, title: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                "/api/v1/threads",
                json={"workspace_id": workspace_id, "title": title},
            ),
        )

    async def list_threads(self, workspace_id: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request("GET", f"/api/v1/workspaces/{workspace_id}/threads"),
        )

    async def list_turns(self, thread_id: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request("GET", f"/api/v1/threads/{thread_id}/turns"),
        )

    async def list_thread_runs(self, thread_id: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request("GET", f"/api/v1/threads/{thread_id}/runs"),
        )

    async def archive_thread(self, thread_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request("POST", f"/api/v1/threads/{thread_id}/archive"),
        )

    async def resume_thread(self, thread_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request("POST", f"/api/v1/threads/{thread_id}/resume"),
        )

    async def fork_thread(self, thread_id: str, title: str | None = None) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request("POST", f"/api/v1/threads/{thread_id}/fork", json={"title": title}),
        )

    async def create_turn(
        self,
        thread_id: str,
        text: str,
        *,
        context_refs: list[dict[str, Any]] | None = None,
        attachment_refs: list[dict[str, Any]] | None = None,
        model_profile: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "input": text,
            "context_refs": context_refs or [],
            "attachment_refs": attachment_refs or [],
        }
        if model_profile:
            payload["model_profile"] = model_profile
        return cast(
            dict[str, Any],
            await self._request("POST", f"/api/v1/threads/{thread_id}/turns", json=payload),
        )

    async def get_run(self, run_id: str) -> dict[str, Any]:
        return cast(dict[str, Any], await self._request("GET", f"/api/v1/runs/{run_id}"))

    async def cancel_run(self, run_id: str) -> dict[str, Any]:
        return cast(dict[str, Any], await self._request("POST", f"/api/v1/runs/{run_id}/cancel"))

    async def replay_run(self, run_id: str) -> dict[str, Any]:
        return cast(dict[str, Any], await self._request("POST", f"/api/v1/runs/{run_id}/replay"))

    async def list_events(self, run_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request("GET", f"/api/v1/runs/{run_id}/events", params={"after": after}),
        )

    async def list_run_steps(self, run_id: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request("GET", f"/api/v1/runs/{run_id}/steps"),
        )

    async def list_run_evidence(self, run_id: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request("GET", f"/api/v1/runs/{run_id}/evidence"),
        )

    async def list_run_claims(self, run_id: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request("GET", f"/api/v1/runs/{run_id}/claims"),
        )

    async def list_run_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request("GET", f"/api/v1/runs/{run_id}/artifacts"),
        )

    async def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request("GET", f"/api/v1/artifacts/{artifact_id}"),
        )

    async def list_workspace_artifacts(self, workspace_id: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request("GET", f"/api/v1/workspaces/{workspace_id}/artifacts"),
        )

    async def download_artifact(self, artifact_id: str) -> bytes:
        response = await self._client.get(f"/api/v1/artifacts/{artifact_id}/content")
        await self._raise_for_status(response)
        return response.content

    async def upload_artifact(
        self,
        workspace_id: str,
        *,
        title: str,
        filename: str,
        content: bytes,
        media_type: str = "application/octet-stream",
        kind: str = "FILE",
        classification: str = "INTERNAL",
        run_id: str | None = None,
        lineage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = {
            "title": title,
            "kind": kind,
            "classification": classification,
            "lineage": json.dumps(lineage or {}),
        }
        if run_id is not None:
            data["run_id"] = run_id
        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                f"/api/v1/workspaces/{workspace_id}/artifacts",
                data=data,
                files={"file": (filename, content, media_type)},
            ),
        )

    async def list_capabilities(self) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request("GET", "/api/v1/capabilities"),
        )

    async def get_capability(self, capability_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request("GET", f"/api/v1/capabilities/{capability_id}"),
        )

    async def invoke_capability(
        self,
        capability_name: str,
        *,
        run_id: str,
        payload: dict[str, Any],
        resource: dict[str, Any],
        environment: str,
        agent_name: str = "general-agent",
        step_id: str | None = None,
        capability_version: int | None = None,
    ) -> dict[str, Any]:
        request = {
            "run_id": run_id,
            "step_id": step_id,
            "payload": payload,
            "resource": resource,
            "environment": environment,
            "agent_name": agent_name,
            "capability_version": capability_version,
        }
        return cast(
            dict[str, Any],
            await self._request(
                "POST", f"/api/v1/capabilities/{capability_name}/invoke", json=request
            ),
        )

    async def create_memory(
        self,
        *,
        scope: str,
        owner_ref: str,
        content: dict[str, Any],
        sensitivity: str = "INTERNAL",
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                "/api/v1/memories",
                json={
                    "scope": scope,
                    "owner_ref": owner_ref,
                    "content": content,
                    "sensitivity": sensitivity,
                    "expires_at": expires_at,
                },
            ),
        )

    async def query_data(
        self,
        thread_id: str,
        question: str,
        *,
        model_profile: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"thread_id": thread_id, "question": question}
        if model_profile is not None:
            payload["model_profile"] = model_profile
        return cast(
            dict[str, Any],
            await self._request("POST", "/api/v1/data/query", json=payload),
        )

    async def search_knowledge(
        self,
        query: str,
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request(
                "POST",
                "/api/v1/knowledge/search",
                json={"query": query, "limit": limit},
            ),
        )

    async def create_evaluation_dataset(
        self,
        *,
        name: str,
        domain: str,
        description: str = "",
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                "/api/v1/admin/evaluations/datasets",
                json={"name": name, "domain": domain, "description": description},
            ),
        )

    async def add_evaluation_case(
        self, dataset_id: str, case: dict[str, Any]
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "POST", f"/api/v1/admin/evaluations/datasets/{dataset_id}/cases", json=case
            ),
        )

    async def run_evaluation(
        self, dataset_id: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "POST", f"/api/v1/admin/evaluations/datasets/{dataset_id}/runs", json=request
            ),
        )

    async def list_evaluation_runs(
        self, *, dataset_id: str | None = None
    ) -> list[dict[str, Any]]:
        params = {"dataset_id": dataset_id} if dataset_id else None
        return cast(
            list[dict[str, Any]],
            await self._request("GET", "/api/v1/admin/evaluations/runs", params=params),
        )

    async def get_evaluation_run(self, run_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request("GET", f"/api/v1/admin/evaluations/runs/{run_id}"),
        )

    async def list_evaluation_results(self, run_id: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request(
                "GET", f"/api/v1/admin/evaluations/runs/{run_id}/results"
            ),
        )

    async def create_workflow(
        self, workspace_id: str, definition: dict[str, Any]
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "POST", f"/api/v1/workspaces/{workspace_id}/workflows", json=definition
            ),
        )

    async def list_workflows(self, workspace_id: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request("GET", f"/api/v1/workspaces/{workspace_id}/workflows"),
        )

    async def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        return cast(dict[str, Any], await self._request("GET", f"/api/v1/workflows/{workflow_id}"))

    async def create_workflow_version(
        self, workflow_id: str, spec: dict[str, Any]
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "POST", f"/api/v1/workflows/{workflow_id}/versions", json={"spec": spec}
            ),
        )

    async def publish_workflow_version(self, workflow_id: str, version: int) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "POST", f"/api/v1/workflows/{workflow_id}/versions/{version}/publish"
            ),
        )

    async def set_workflow_status(self, workflow_id: str, action: str) -> dict[str, Any]:
        if action not in {"pause", "activate", "retire"}:
            raise ValueError("workflow action must be pause, activate, or retire")
        return cast(
            dict[str, Any],
            await self._request("POST", f"/api/v1/workflows/{workflow_id}/{action}"),
        )

    async def create_schedule(self, workflow_id: str, schedule: dict[str, Any]) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "POST", f"/api/v1/workflows/{workflow_id}/schedules", json=schedule
            ),
        )

    async def list_schedules(self, workflow_id: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request("GET", f"/api/v1/workflows/{workflow_id}/schedules"),
        )

    async def set_schedule_enabled(self, schedule_id: str, enabled: bool) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "PATCH", f"/api/v1/automation/schedules/{schedule_id}", json={"enabled": enabled}
            ),
        )

    async def trigger_workflow(
        self,
        workflow_id: str,
        *,
        input_payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                f"/api/v1/workflows/{workflow_id}/trigger",
                json={
                    "input_payload": input_payload or {},
                    "idempotency_key": idempotency_key,
                },
            ),
        )

    async def list_workflow_executions(
        self, workflow_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request(
                "GET", f"/api/v1/workflows/{workflow_id}/executions", params={"limit": limit}
            ),
        )

    async def get_automation_execution(self, execution_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request("GET", f"/api/v1/automation/executions/{execution_id}"),
        )

    async def cancel_automation_execution(self, execution_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request("POST", f"/api/v1/automation/executions/{execution_id}/cancel"),
        )

    async def review_automation_step(
        self, step_id: str, *, decision: str, reason: str
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                f"/api/v1/automation/steps/{step_id}/review",
                json={"decision": decision, "reason": reason},
            ),
        )

    async def list_notifications(
        self, *, unread_only: bool = False, limit: int = 100
    ) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request(
                "GET",
                "/api/v1/notifications",
                params={"unread_only": unread_only, "limit": limit},
            ),
        )

    async def mark_notification_read(self, notification_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request("POST", f"/api/v1/notifications/{notification_id}/read"),
        )

    async def create_action(self, workspace_id: str, request: dict[str, Any]) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request("POST", f"/api/v1/workspaces/{workspace_id}/actions", json=request),
        )

    async def list_actions(
        self,
        workspace_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if status is not None:
            params["status"] = status
        return cast(
            list[dict[str, Any]],
            await self._request("GET", f"/api/v1/workspaces/{workspace_id}/actions", params=params),
        )

    async def get_action(self, action_id: str) -> dict[str, Any]:
        return cast(dict[str, Any], await self._request("GET", f"/api/v1/actions/{action_id}"))

    async def preflight_action(
        self,
        action_id: str,
        *,
        reason: str,
        approval_ttl_minutes: int = 60,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                f"/api/v1/actions/{action_id}/preflight",
                json={"reason": reason, "approval_ttl_minutes": approval_ttl_minutes},
            ),
        )

    async def list_action_approvals(
        self, *, status: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if status is not None:
            params["status"] = status
        return cast(
            list[dict[str, Any]],
            await self._request("GET", "/api/v1/action-approvals", params=params),
        )

    async def decide_action_approval(
        self, approval_id: str, *, approve: bool, reason: str
    ) -> dict[str, Any]:
        decision = "approve" if approve else "reject"
        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                f"/api/v1/action-approvals/{approval_id}/{decision}",
                json={"reason": reason},
            ),
        )

    async def request_action_rollback(
        self,
        action_id: str,
        *,
        reason: str,
        approval_ttl_minutes: int = 60,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                f"/api/v1/actions/{action_id}/rollback",
                json={"reason": reason, "approval_ttl_minutes": approval_ttl_minutes},
            ),
        )

    async def cancel_action(self, action_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request("POST", f"/api/v1/actions/{action_id}/cancel"),
        )

    async def list_action_events(
        self, action_id: str, *, after: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request(
                "GET",
                f"/api/v1/actions/{action_id}/events",
                params={"after": after, "limit": limit},
            ),
        )

    async def decide_memory(self, memory_id: str, *, approve: bool, reason: str) -> dict[str, Any]:
        action = "approve" if approve else "reject"
        return cast(
            dict[str, Any],
            await self._request(
                "POST", f"/api/v1/memories/{memory_id}/{action}", json={"reason": reason}
            ),
        )

    async def stream_events(self, run_id: str, *, after: int = 0) -> AsyncIterator[dict[str, Any]]:
        headers = {"Accept": "text/event-stream", "Last-Event-ID": str(after)}
        async with self._client.stream(
            "GET", f"/api/v1/runs/{run_id}/events/stream", params={"after": after}, headers=headers
        ) as response:
            await self._raise_for_status(response)
            data_lines: list[str] = []
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif not line and data_lines:
                    yield cast(dict[str, Any], json.loads("\n".join(data_lines)))
                    data_lines.clear()
            if data_lines:
                yield cast(dict[str, Any], json.loads("\n".join(data_lines)))

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(method, path, **kwargs)
        await self._raise_for_status(response)
        return response.json()

    @staticmethod
    async def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            body = response.json()
        except ValueError:
            body = {}
        raise ObsionAPIError(
            response.status_code,
            str(body.get("code", "http_error")),
            str(body.get("message", "Obsion API request failed")),
            str(body.get("correlation_id", response.headers.get("X-Request-ID", ""))),
        )
