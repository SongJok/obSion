"""Operator-only independent scoring of an actual, authorized published answer."""

import hashlib
import json
from decimal import Decimal
from time import perf_counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import ObsionError
from obsion.common.ids import new_id
from obsion.db.models import Artifact, Thread, Turn, Workspace
from obsion.domain.enums import (
    ActorType,
    ArtifactKind,
    Classification,
    DecisionEffect,
    RiskLevel,
    RunStatus,
)
from obsion.evaluations.acceptance import Score
from obsion.evaluations.engine import canonical_sha256
from obsion.evaluations.semantic import (
    POLICY,
    POLICY_SHA256,
    SemanticScoreRequest,
    judgment_diagnostic,
    score_input,
    semantic_score,
)
from obsion.knowledge.service import KnowledgeService
from obsion.model_gateway.gateway import ModelGateway, ModelUnavailableError
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal
from obsion.security.policy import PolicyEngine, ResourcePolicyInput
from obsion.security.redaction import redact
from obsion.security.source_fence import acquire_source_publication_fence
from obsion.security.workspace_access import require_run_access


class AnswerScoringService:
    def __init__(self, knowledge: KnowledgeService, models: ModelGateway) -> None:
        self.knowledge = knowledge
        self.models = models
        self.policy = PolicyEngine()

    async def _inputs(
        self, session: AsyncSession, principal: Principal, request: SemanticScoreRequest
    ) -> tuple[str, str, Classification]:
        run = await require_run_access(session, principal, request.run_id, source_content=True)
        if run.status != RunStatus.COMPLETED:
            raise ValueError("candidate_not_completed")
        turn = await session.scalar(
            select(Turn)
            .where(Turn.id == run.turn_id, Turn.organization_id == principal.organization_id)
            .execution_options(populate_existing=True)
        )
        if turn is None or turn.input_text != redact(request.case.question):
            raise ValueError("candidate_question_mismatch")
        # The server reads the same unsuperseded TEXT artifact as the acceptance driver.
        artifacts = list(
            await session.scalars(
                select(Artifact)
                .where(
                    Artifact.run_id == run.id,
                    Artifact.organization_id == principal.organization_id,
                    Artifact.kind == ArtifactKind.TEXT,
                    Artifact.superseded_at.is_(None),
                )
                .execution_options(populate_existing=True)
            )
        )
        answers = [
            item
            for item in artifacts
            if isinstance(item.inline_content, dict)
            and isinstance(item.inline_content.get("markdown"), str)
        ]
        if len(answers) != 1:
            raise ValueError("published_answer_ambiguous")
        answer = str((answers[0].inline_content or {})["markdown"])
        if (
            not answer.strip()
            or hashlib.sha256(answer.encode()).hexdigest() != request.answer_sha256
        ):
            raise ValueError("published_answer_changed")
        document, _, stored = await self.knowledge.get_content(
            session, principal, request.case.source_document_id
        )
        if hashlib.sha256(stored.data).hexdigest() != request.case.source_sha256:
            raise ValueError("reviewed_source_changed")
        source = stored.data.decode("utf-8")
        if any(
            not quote.strip() or quote not in source
            for quote in request.case.reviewed_source_quotes
        ):
            raise ValueError("reviewed_source_quote_invalid")
        levels = list(Classification)
        workspace_classification = await session.scalar(
            select(Workspace.classification)
            .join(Thread, Thread.workspace_id == Workspace.id)
            .join(Turn, Turn.thread_id == Thread.id)
            .where(Turn.id == run.turn_id, Workspace.organization_id == principal.organization_id)
        )
        if workspace_classification is None:
            raise ValueError("candidate_workspace_unavailable")
        classification = max(
            (document.classification, answers[0].classification, workspace_classification),
            key=levels.index,
        )
        return answer, source, classification

    async def score(
        self, session: AsyncSession, principal: Principal, request: SemanticScoreRequest
    ) -> Score:
        started = perf_counter()
        correlation_id = new_id()
        scorer_id = f"obsion-semantic-v1:{request.model_profile_id}:{POLICY_SHA256}"
        score = Score(
            status="BLOCKED",
            reason="independent_inputs_unavailable",
            scorer_id=scorer_id,
            answer_sha256=request.answer_sha256,
        )
        decision_ids: list[str] = []
        call_id: UUID | None = None
        call_ids: tuple[UUID, ...] = ()
        classification: Classification | None = None
        diagnostic: str | None = None

        async def authorize(current: Principal, stage: str) -> bool:
            decision = await self.policy.evaluate_resource(
                session,
                ResourcePolicyInput(
                    principal=current,
                    action="evaluations.write",
                    resource_type="answer_evaluation",
                    resource={
                        "run_id": str(request.run_id),
                        "source_document_id": str(request.case.source_document_id),
                        "model_profile_id": str(request.model_profile_id),
                    },
                    context={"stage": stage, "evaluation_id": str(correlation_id)},
                    risk_level=RiskLevel.L1,
                ),
            )
            decision_ids.append(str(decision.id))
            return decision.effect == DecisionEffect.ALLOW and not decision.obligations

        try:
            if not await authorize(principal, "before"):
                score = score.model_copy(update={"reason": "independent_scoring_denied"})
            elif request.policy_sha256 != POLICY_SHA256:
                score = score.model_copy(update={"reason": "independent_policy_changed"})
            else:
                answer, source, classification = await self._inputs(session, principal, request)
                payload = score_input(request.case, answer, source)
                serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                if (
                    len(serialized) > 120_000
                    or len(request.case.scoring_rules) > 40
                    or redact(payload) != payload
                ):
                    score = score.model_copy(
                        update={"reason": "independent_input_budget_or_redaction"}
                    )
                else:
                    # No tools, memory, candidate claims, retries, or candidate Run mutation.
                    # Null run_id already exists for standalone Model Gateway operations.
                    result = await self.models.complete(
                        session,
                        organization_id=principal.organization_id,
                        run_id=None,
                        step_id=None,
                        profile_id=request.model_profile_id,
                        messages=[
                            {"role": "system", "content": POLICY},
                            {"role": "user", "content": serialized},
                        ],
                        classification=classification,
                        json_mode=True,
                        temperature=0,
                        max_input_tokens=32000,
                        max_output_tokens=4000,
                        max_cost_amount=Decimal("0.25"),
                        timeout_seconds=45,
                    )
                    call_id = result.call_id
                    call_ids = result.call_ids
                    if result.finish_reason in {"length", "max_tokens"}:
                        score = score.model_copy(update={"reason": "independent_output_truncated"})
                    elif result.profile_id != request.model_profile_id:
                        score = score.model_copy(update={"reason": "independent_model_changed"})
                    else:
                        try:
                            score = semantic_score(
                                result.content,
                                case=request.case,
                                answer=answer,
                                source=source,
                                scorer_id=scorer_id,
                            )
                        except (ValueError, TypeError, RecursionError) as exc:
                            diagnostic = judgment_diagnostic(exc)
                            score = score.model_copy(
                                update={"reason": "independent_judgment_invalid"}
                            )
                    # Recheck identities, permission, content and policy after a slow model call.
                    # Serialize final checks and score commit with real authorization writes.
                    # The short publication fence is deliberately absent during model work.
                    await acquire_source_publication_fence(session, principal.organization_id)
                    current = await load_principal_by_id(
                        session, principal.organization_id, principal.id
                    )
                    if not await authorize(current, "after"):
                        score = score.model_copy(
                            update={
                                "status": "BLOCKED",
                                "reason": "independent_scoring_denied",
                                "evidence": [],
                            }
                        )
                    else:
                        if await self._inputs(session, current, request) != (
                            answer,
                            source,
                            classification,
                        ):
                            raise ValueError("independent_inputs_changed")
        except ModelUnavailableError as exc:
            call_ids = exc.call_ids
            score = score.model_copy(
                update={
                    "status": "BLOCKED",
                    "reason": "independent_model_timeout"
                    if exc.timed_out
                    else "independent_model_unavailable",
                    "evidence": [],
                }
            )
        except (ObsionError, ValueError, TypeError):
            score = score.model_copy(
                update={
                    "status": "BLOCKED",
                    "reason": "independent_inputs_or_model_unavailable",
                    "evidence": [],
                }
            )
        audit = await AuditWriter().write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action="evaluation.answer.score",
                resource_type="run",
                resource_id=str(request.run_id),
                outcome=score.status,
                policy_decision_id=UUID(decision_ids[-1]) if decision_ids else None,
                model_profile_id=request.model_profile_id,
                result_classification=classification,
                latency_ms=int((perf_counter() - started) * 1000),
                metadata={
                    "request_sha256": canonical_sha256(request.model_dump(mode="json")),
                    "policy_sha256": POLICY_SHA256,
                    "policy_decision_ids": decision_ids,
                    "model_call_id": str(call_id) if call_id else None,
                    "model_call_ids": [str(value) for value in call_ids],
                    "score": score.model_dump(mode="json"),
                    "judgment_diagnostic": diagnostic,
                },
            ),
        )
        return score.model_copy(
            update={
                "evidence": [
                    *score.evidence,
                    {
                        "audit_id": str(audit.id),
                        "evaluation_id": str(correlation_id),
                        "model_call_id": str(call_id) if call_id else None,
                        "model_call_ids": [str(value) for value in call_ids],
                        "policy_decision_ids": decision_ids,
                        "judgment_diagnostic": diagnostic,
                    },
                ]
            }
        )
