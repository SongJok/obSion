from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from obsion_cli.config import CliError, CliSettings
from obsion_sdk import (
    AsyncObsionAppServerClient,
    AsyncObsionClient,
    app_server_url_from_api_url,
    new_client_request_id,
)
from obsion_sdk.app_server import TransportFactory

_TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
Sleep = Callable[[float], Awaitable[None]]
RequestIdFactory = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class AskResult:
    workspace: dict[str, Any]
    thread: dict[str, Any]
    turn: dict[str, Any]
    run: dict[str, Any]
    events: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    answer: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "artifacts": self.artifacts,
            "claims": self.claims,
            "events": self.events,
            "evidence": self.evidence,
            "run": self.run,
            "steps": self.steps,
            "thread": self.thread,
            "turn": self.turn,
            "workspace": self.workspace,
        }


class ExperienceRuntime:
    """Experience client over REST and App Server. This is not a Harness."""

    def __init__(
        self,
        settings: CliSettings,
        *,
        rest: AsyncObsionClient,
        app_server: AsyncObsionAppServerClient | None = None,
        request_id_factory: RequestIdFactory | None = None,
        sleep: Sleep | None = None,
    ) -> None:
        if settings.uses_app_server and app_server is None:
            raise CliError("App Server protocol requires an App Server client")
        self.settings = settings
        self.rest = rest
        self.app_server = app_server
        self._request_id = request_id_factory or new_client_request_id
        self._sleep = sleep or asyncio.sleep

    @classmethod
    async def connect(
        cls,
        settings: CliSettings,
        *,
        rest: AsyncObsionClient | None = None,
        app_server: AsyncObsionAppServerClient | None = None,
        transport_factory: TransportFactory | None = None,
    ) -> ExperienceRuntime:
        rest_client = rest or AsyncObsionClient(settings.base_url, token=settings.token)
        server = app_server
        if settings.uses_app_server and server is None:
            server = AsyncObsionAppServerClient(
                app_server_url_from_api_url(settings.base_url),
                token=settings.token,
                client_name="obsion-cli",
                client_version="0.1.0",
                transport_factory=transport_factory,
            )
            await server.connect()
        return cls(settings, rest=rest_client, app_server=server)

    async def aclose(self) -> None:
        await self.rest.aclose()
        if self.app_server is not None:
            await self.app_server.aclose()

    async def list_workspaces(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        if self.app_server is not None:
            return await self.app_server.list_workspaces(include_archived=include_archived)
        return await self.rest.list_workspaces(include_archived=include_archived)

    async def create_workspace(self, name: str, *, description: str = "") -> dict[str, Any]:
        return await self.rest.create_workspace(name, description=description)

    async def list_threads(
        self, workspace_id: str, *, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        if self.app_server is not None:
            return await self.app_server.list_threads(
                workspace_id, include_archived=include_archived
            )
        return await self.rest.list_threads(workspace_id, include_archived=include_archived)

    async def create_thread(self, workspace_id: str, title: str) -> dict[str, Any]:
        if self.app_server is not None:
            return await self.app_server.create_thread(
                workspace_id,
                title,
                client_request_id=self._request_id("thread"),
            )
        return await self.rest.create_thread(workspace_id, title)

    async def archive_thread(self, thread_id: str) -> dict[str, Any]:
        if self.app_server is not None:
            return await self.app_server.archive_thread(
                thread_id, client_request_id=self._request_id("archive")
            )
        return await self.rest.archive_thread(thread_id)

    async def resume_thread(self, thread_id: str) -> dict[str, Any]:
        if self.app_server is not None:
            return await self.app_server.resume_thread(
                thread_id, client_request_id=self._request_id("resume")
            )
        return await self.rest.resume_thread(thread_id)

    async def fork_thread(
        self, thread_id: str, *, title: str | None = None, from_turn_id: str | None = None
    ) -> dict[str, Any]:
        if self.app_server is not None:
            return await self.app_server.fork_thread(
                thread_id,
                client_request_id=self._request_id("fork"),
                title=title,
                from_turn_id=from_turn_id,
            )
        return await self.rest.fork_thread(thread_id, title, from_turn_id=from_turn_id)

    async def create_turn(self, thread_id: str, text: str) -> dict[str, Any]:
        if self.app_server is not None:
            return await self.app_server.create_turn(
                thread_id,
                text,
                client_request_id=self._request_id("turn"),
            )
        return await self.rest.create_turn(thread_id, text)

    async def get_run(self, run_id: str) -> dict[str, Any]:
        if self.app_server is not None:
            return await self.app_server.get_run(run_id)
        return await self.rest.get_run(run_id)

    async def cancel_run(self, run_id: str) -> dict[str, Any]:
        if self.app_server is not None:
            return await self.app_server.cancel_run(
                run_id, client_request_id=self._request_id("cancel")
            )
        return await self.rest.cancel_run(run_id)

    async def replay_run(self, run_id: str) -> dict[str, Any]:
        if self.app_server is not None:
            return await self.app_server.replay_run(
                run_id, client_request_id=self._request_id("replay")
            )
        return await self.rest.replay_run(run_id)

    async def list_run_events(self, run_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        return await self.rest.list_events(run_id, after=after)

    async def list_run_steps(self, run_id: str) -> list[dict[str, Any]]:
        return await self.rest.list_run_steps(run_id)

    async def list_run_evidence(self, run_id: str) -> list[dict[str, Any]]:
        return await self.rest.list_run_evidence(run_id)

    async def list_run_claims(self, run_id: str) -> list[dict[str, Any]]:
        return await self.rest.list_run_claims(run_id)

    async def list_run_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        return await self.rest.list_run_artifacts(run_id)

    async def list_approvals(self, *, status: str | None = None) -> list[dict[str, Any]]:
        if self.app_server is not None:
            return await self.app_server.list_approvals(status=status)
        return await self.rest.list_approvals(status=status)

    async def decide_approval(
        self, approval_id: str, *, approve: bool, reason: str
    ) -> dict[str, Any]:
        if self.app_server is not None:
            return await self.app_server.decide_approval(
                approval_id,
                client_request_id=self._request_id("approval"),
                approve=approve,
                reason=reason,
            )
        return await self.rest.decide_approval(approval_id, approve=approve, reason=reason)

    async def ask(
        self,
        text: str,
        *,
        workspace_id: str | None = None,
        workspace_name: str | None = None,
        thread_id: str | None = None,
        title: str | None = None,
    ) -> AskResult:
        question = text.strip()
        if not question:
            raise CliError("Ask requires a non-empty question")
        workspace = await self._resolve_workspace(workspace_id, workspace_name)
        thread = await self._resolve_thread(
            str(workspace["id"]),
            thread_id,
            title or _thread_title(question),
        )
        created = await self.create_turn(str(thread["id"]), question)
        turn = _mapping(created.get("turn"), created)
        run = _mapping(created.get("run"), created)
        run_id = str(run.get("id") or "")
        if not run_id:
            raise CliError("Turn creation did not return a Run")
        run, events = await self.wait_for_run(run_id)
        steps, evidence, claims, artifacts = await asyncio.gather(
            self.list_run_steps(run_id),
            self.list_run_evidence(run_id),
            self.list_run_claims(run_id),
            self.list_run_artifacts(run_id),
        )
        return AskResult(
            workspace=workspace,
            thread=thread,
            turn=turn,
            run=run,
            events=events,
            steps=steps,
            evidence=evidence,
            claims=claims,
            artifacts=artifacts,
            answer=_answer_from(events, artifacts),
        )

    async def wait_for_run(self, run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        deadline = time.monotonic() + self.settings.wait_timeout_seconds
        after = 0
        events: list[dict[str, Any]] = []
        seen: set[str] = set()
        while time.monotonic() < deadline:
            run = await self.get_run(run_id)
            batch = await self.list_run_events(run_id, after=after)
            for event in batch:
                event_id = str(event.get("id") or "")
                if event_id:
                    if event_id in seen:
                        continue
                    seen.add(event_id)
                events.append(event)
                sequence = event.get("run_sequence")
                if isinstance(sequence, int) and sequence > after:
                    after = sequence
            if str(run.get("status")) in _TERMINAL:
                return run, events
            await self._sleep(self.settings.poll_interval_seconds)
        raise CliError(f"Timed out waiting for run {run_id}")

    async def _resolve_workspace(
        self, workspace_id: str | None, workspace_name: str | None
    ) -> dict[str, Any]:
        if workspace_id:
            for item in await self.list_workspaces(include_archived=True):
                if str(item.get("id")) == workspace_id:
                    return item
            raise CliError(f"Workspace {workspace_id} was not found")
        name = (workspace_name or "CLI").strip() or "CLI"
        for item in await self.list_workspaces():
            if str(item.get("name")) == name:
                return item
        return await self.create_workspace(name, description="Obsion Experience CLI workspace")

    async def _resolve_thread(
        self, workspace_id: str, thread_id: str | None, title: str
    ) -> dict[str, Any]:
        if thread_id:
            for item in await self.list_threads(workspace_id, include_archived=True):
                if str(item.get("id")) == thread_id:
                    if item.get("status") == "ARCHIVED":
                        return await self.resume_thread(thread_id)
                    return item
            raise CliError(f"Thread {thread_id} was not found")
        return await self.create_thread(workspace_id, title)


def _thread_title(question: str) -> str:
    first_line = question.splitlines()[0].strip()
    return first_line[:80] or "CLI question"


def _mapping(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    return value if isinstance(value, dict) else fallback


def _answer_from(events: list[dict[str, Any]], artifacts: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for event in events:
        if event.get("name") != "answer.delta":
            continue
        payload = event.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("delta"), str):
            chunks.append(payload["delta"])
    if chunks:
        return "".join(chunks)
    for artifact in artifacts:
        content = artifact.get("inline_content")
        if not isinstance(content, dict):
            continue
        markdown = content.get("markdown")
        if isinstance(markdown, str):
            return markdown
        text = content.get("text")
        if isinstance(text, str):
            return text
    return ""
