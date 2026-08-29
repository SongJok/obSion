import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import NotFoundError
from obsion.config import Settings
from obsion.db.models import (
    Artifact,
    Run,
    RunConversationSnapshot,
    Thread,
    Turn,
    Workspace,
)
from obsion.domain.enums import ArtifactKind, Classification, RunStatus
from obsion.security.identity import Principal
from obsion.security.redaction import redact_text
from obsion.telemetry import conversation_context_counter

_CLASSIFICATION_ORDER = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
    Classification.RESTRICTED: 3,
}


@dataclass(frozen=True, slots=True)
class _ConversationTurn:
    turn: Turn
    source_run_id: UUID | None
    source_artifact_id: UUID | None
    user_content: str
    assistant_content: str | None
    classification: Classification


class ConversationContextService:
    """Capture bounded prior Thread context as an immutable Run input."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def capture(
        self,
        session: AsyncSession,
        principal: Principal,
        run: Run,
        current_turn: Turn,
        thread: Thread,
        effective_prior_turns: list[Turn],
    ) -> list[RunConversationSnapshot]:
        if not effective_prior_turns:
            return []
        workspace_classification = await session.scalar(
            select(Workspace.classification).where(
                Workspace.id == thread.workspace_id,
                Workspace.organization_id == principal.organization_id,
            )
        )
        if workspace_classification is None:
            raise NotFoundError("Workspace", thread.workspace_id)

        candidates = await self._candidates(
            session,
            principal.organization_id,
            effective_prior_turns,
            current_turn.created_at,
            workspace_classification,
        )
        selected = self._bounded(candidates)
        captured_at = current_turn.created_at
        snapshots = [
            RunConversationSnapshot(
                organization_id=principal.organization_id,
                run_id=run.id,
                source_thread_id=item.turn.thread_id,
                source_turn_id=item.turn.id,
                source_run_id=item.source_run_id,
                source_artifact_id=item.source_artifact_id,
                source_principal_id=item.turn.created_by,
                ordinal=ordinal,
                user_content=item.user_content,
                assistant_content=item.assistant_content,
                content_fingerprint=self._fingerprint(item, ordinal),
                classification=item.classification,
                captured_at=captured_at,
            )
            for ordinal, item in enumerate(selected, start=1)
        ]
        session.add_all(snapshots)
        await session.flush()
        conversation_context_counter.add(
            len(snapshots),
            {
                "status": "CAPTURED",
                "branch": str(thread.parent_thread_id is not None).lower(),
            },
        )
        return snapshots

    async def _candidates(
        self,
        session: AsyncSession,
        organization_id: UUID,
        turns: list[Turn],
        captured_at: datetime,
        workspace_classification: Classification,
    ) -> list[_ConversationTurn]:
        turn_ids = [item.id for item in turns]
        runs = list(
            await session.scalars(
                select(Run)
                .where(
                    Run.organization_id == organization_id,
                    Run.turn_id.in_(turn_ids),
                    Run.status == RunStatus.COMPLETED,
                    Run.completed_at.is_not(None),
                    Run.completed_at <= captured_at,
                )
                .order_by(Run.completed_at.desc(), Run.created_at.desc(), Run.id.desc())
            )
        )
        run_ids = [item.id for item in runs]
        artifacts = (
            list(
                await session.scalars(
                    select(Artifact)
                    .where(
                        Artifact.organization_id == organization_id,
                        Artifact.run_id.in_(run_ids),
                        Artifact.kind == ArtifactKind.TEXT,
                        Artifact.media_type == "text/markdown",
                        Artifact.created_at <= captured_at,
                    )
                    .order_by(Artifact.created_at.desc(), Artifact.id.desc())
                )
            )
            if run_ids
            else []
        )
        answer_by_run: dict[UUID, tuple[Artifact, str]] = {}
        for artifact in artifacts:
            if artifact.run_id is None or artifact.run_id in answer_by_run:
                continue
            content = artifact.inline_content or {}
            markdown = content.get("markdown") if isinstance(content, dict) else None
            if isinstance(markdown, str) and markdown.strip():
                answer_by_run[artifact.run_id] = (artifact, redact_text(markdown))

        runs_by_turn: dict[UUID, list[Run]] = {}
        for source_run in runs:
            if source_run.id in answer_by_run:
                runs_by_turn.setdefault(source_run.turn_id, []).append(source_run)

        candidates: list[_ConversationTurn] = []
        for turn in turns:
            selected_run: Run | None = next(iter(runs_by_turn.get(turn.id, [])), None)
            artifact_answer = (
                answer_by_run.get(selected_run.id) if selected_run is not None else None
            )
            selected_artifact: Artifact | None = (
                artifact_answer[0] if artifact_answer is not None else None
            )
            answer = artifact_answer[1] if artifact_answer is not None else None
            classification = self._highest_classification(
                workspace_classification,
                selected_artifact.classification if selected_artifact is not None else None,
            )
            candidates.append(
                _ConversationTurn(
                    turn=turn,
                    source_run_id=selected_run.id if selected_run is not None else None,
                    source_artifact_id=(
                        selected_artifact.id if selected_artifact is not None else None
                    ),
                    user_content=turn.sanitized_input,
                    assistant_content=answer,
                    classification=classification,
                )
            )
        return candidates

    def _bounded(self, candidates: list[_ConversationTurn]) -> list[_ConversationTurn]:
        remaining = self.settings.conversation_context_max_chars
        per_message = self.settings.conversation_context_max_chars_per_message
        selected: list[_ConversationTurn] = []
        for item in reversed(candidates[-self.settings.conversation_context_max_turns :]):
            if remaining <= 0:
                break
            user_content = item.user_content[:per_message][:remaining]
            remaining -= len(user_content)
            assistant_content: str | None = None
            if item.assistant_content and remaining > 0:
                assistant_content = item.assistant_content[:per_message][:remaining]
                remaining -= len(assistant_content)
            if not user_content and not assistant_content:
                continue
            selected.append(
                _ConversationTurn(
                    turn=item.turn,
                    source_run_id=item.source_run_id,
                    source_artifact_id=item.source_artifact_id,
                    user_content=user_content,
                    assistant_content=assistant_content,
                    classification=item.classification,
                )
            )
        selected.reverse()
        return selected

    @staticmethod
    def _highest_classification(
        workspace: Classification,
        artifact: Classification | None,
    ) -> Classification:
        if (
            artifact is not None
            and _CLASSIFICATION_ORDER[artifact] > _CLASSIFICATION_ORDER[workspace]
        ):
            return artifact
        return workspace

    @staticmethod
    def _fingerprint(item: _ConversationTurn, ordinal: int) -> str:
        payload = {
            "ordinal": ordinal,
            "source_thread_id": str(item.turn.thread_id),
            "source_turn_id": str(item.turn.id),
            "source_run_id": str(item.source_run_id) if item.source_run_id else None,
            "source_artifact_id": (
                str(item.source_artifact_id) if item.source_artifact_id else None
            ),
            "source_principal_id": str(item.turn.created_by),
            "user_content": item.user_content,
            "assistant_content": item.assistant_content,
            "classification": item.classification,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()
