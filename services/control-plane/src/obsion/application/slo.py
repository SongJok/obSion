from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.db.models import (
    Approval,
    Event,
    ModelCall,
    Run,
    RunFeedback,
    RunStep,
    VerificationAssessment,
)
from obsion.domain.enums import (
    ApprovalStatus,
    RunFeedbackRating,
    RunStatus,
    StepKind,
)

_TERMINAL = (
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


class RuntimeSloService:
    """Project goal.txt core metrics from PostgreSQL."""

    async def project(self, session: AsyncSession, organization_id: UUID) -> dict[str, Any]:
        status_rows = (
            await session.execute(
                select(Run.status, func.count(Run.id))
                .where(Run.organization_id == organization_id)
                .group_by(Run.status)
            )
        ).all()
        status_counts = {str(status): int(count) for status, count in status_rows}
        completed = status_counts.get(RunStatus.COMPLETED.value, 0)
        failed = status_counts.get(RunStatus.FAILED.value, 0)
        cancelled = status_counts.get(RunStatus.CANCELLED.value, 0)
        terminal = completed + failed + cancelled

        usage = (
            await session.execute(
                select(
                    func.coalesce(func.sum(Run.input_tokens), 0),
                    func.coalesce(func.sum(Run.output_tokens), 0),
                    func.coalesce(func.sum(Run.cost_amount), 0),
                    func.coalesce(func.avg(Run.step_count), 0),
                    func.count(Run.id),
                ).where(
                    Run.organization_id == organization_id,
                    Run.status.in_(_TERMINAL),
                )
            )
        ).one()
        input_tokens, output_tokens, cost_amount, average_steps, usage_count = usage

        timings = (
            await session.execute(
                select(Run.started_at, Run.completed_at).where(
                    Run.organization_id == organization_id,
                    Run.status.in_(_TERMINAL),
                    Run.started_at.is_not(None),
                    Run.completed_at.is_not(None),
                )
            )
        ).all()
        latency_ms = [
            (completed_at - started_at).total_seconds() * 1000
            for started_at, completed_at in timings
            if started_at is not None
            and completed_at is not None
            and (completed_at - started_at).total_seconds() >= 0
        ]

        model_row = (
            await session.execute(
                select(
                    func.avg(ModelCall.latency_ms),
                    func.count(ModelCall.id),
                ).where(ModelCall.organization_id == organization_id)
            )
        ).one()
        model_avg, model_count = model_row

        tool_timings = (
            await session.execute(
                select(RunStep.started_at, RunStep.completed_at).where(
                    RunStep.organization_id == organization_id,
                    RunStep.kind == StepKind.CAPABILITY,
                    RunStep.started_at.is_not(None),
                    RunStep.completed_at.is_not(None),
                )
            )
        ).all()
        tool_ms = [
            (completed_at - started_at).total_seconds() * 1000
            for started_at, completed_at in tool_timings
            if started_at is not None
            and completed_at is not None
            and (completed_at - started_at).total_seconds() >= 0
        ]

        replans = int(
            await session.scalar(
                select(func.count(Event.id)).where(
                    Event.organization_id == organization_id,
                    Event.name == "plan.updated",
                )
            )
            or 0
        )
        approval_rows = (
            await session.execute(
                select(Approval.status, func.count(Approval.id))
                .where(Approval.organization_id == organization_id)
                .group_by(Approval.status)
            )
        ).all()
        approval_counts = {str(status): int(count) for status, count in approval_rows}
        approved = approval_counts.get(ApprovalStatus.APPROVED.value, 0)
        rejected = approval_counts.get(ApprovalStatus.REJECTED.value, 0)
        decided = approved + rejected

        feedback_rows = (
            await session.execute(
                select(RunFeedback.rating, func.count(RunFeedback.id))
                .where(RunFeedback.organization_id == organization_id)
                .group_by(RunFeedback.rating)
            )
        ).all()
        feedback_counts = {str(rating): int(count) for rating, count in feedback_rows}
        helpful = feedback_counts.get(RunFeedbackRating.HELPFUL.value, 0)
        needs_improvement = feedback_counts.get(RunFeedbackRating.NEEDS_IMPROVEMENT.value, 0)
        feedback_total = helpful + needs_improvement

        coverage_row = (
            await session.execute(
                select(
                    func.avg(VerificationAssessment.coverage),
                    func.count(VerificationAssessment.id),
                ).where(VerificationAssessment.organization_id == organization_id)
            )
        ).one()
        coverage_avg, coverage_count = coverage_row

        return {
            "source": "postgresql",
            "runs": {
                "terminal": terminal,
                "completed": completed,
                "failed": failed,
                "cancelled": cancelled,
                "success_rate": _rate(completed, terminal),
            },
            "latency": {
                "average_ms": _average(latency_ms),
                "count": len(latency_ms),
                "ttft": {
                    "available": False,
                    "metric": "obsion.run.ttft",
                    "reason": "histogram-only",
                },
                "model": {
                    "average_ms": (round(float(model_avg), 2) if model_avg is not None else None),
                    "count": int(model_count or 0),
                },
                "tool": {
                    "average_ms": _average(tool_ms),
                    "count": len(tool_ms),
                    "source": "capability-steps",
                },
            },
            "steps": {
                "average": round(float(average_steps or 0), 4) if usage_count else None,
                "count": int(usage_count or 0),
            },
            "tokens": {
                "input": int(input_tokens or 0),
                "output": int(output_tokens or 0),
            },
            "cost": {"amount": str(cost_amount if cost_amount is not None else Decimal("0"))},
            "replans": {
                "events": replans,
                "rate": _rate(replans, terminal),
            },
            "approvals": {
                "requested": sum(approval_counts.values()),
                "approved": approved,
                "rejected": rejected,
                "pending": approval_counts.get(ApprovalStatus.PENDING.value, 0),
                "approval_rate": _rate(approved, decided),
            },
            "satisfaction": {
                "total": feedback_total,
                "helpful": helpful,
                "needs_improvement": needs_improvement,
                "helpful_rate": _rate(helpful, feedback_total),
            },
            "evidence_coverage": {
                "average": (round(float(coverage_avg), 4) if coverage_avg is not None else None),
                "count": int(coverage_count or 0),
            },
        }
