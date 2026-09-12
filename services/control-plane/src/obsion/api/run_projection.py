"""Keep task operations visible without exposing revoked planning context."""

from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import RunView
from obsion.db.models import Run
from obsion.knowledge.publication import KnowledgePublicationGuard
from obsion.security.identity import Principal


async def public_run_view(
    session: AsyncSession, principal: Principal, run: Run, *, snapshot: RunView | None = None
) -> RunView:
    # Idempotent replies retain their original outcome, but never their old grant.
    view = snapshot if snapshot is not None else RunView.model_validate(run)
    available = await KnowledgePublicationGuard().check(
        session,
        principal,
        [],
        run_id=run.id,
        stage="run_metadata",
        # Model work may hold this Run row FOR UPDATE. Retain the Run identifier
        # in the Policy resource and audit correlation without an FK write that
        # would block a read-only progress poll on the model transaction.
        link_policy_run=False,
    )
    if available:
        return view
    return view.model_copy(
        update={
            "source_content_available": False,
            "intent": {},
            "plan": {},
            "pending_clarification": None,
            "context_budget": {},
            "conversation_compact": {},
            "workspace_context": {},
            "prompt_pins": [],
            "error_message": None,
        }
    )
