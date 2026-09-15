"""Call only the authenticated Obsion scoring API, never a model provider."""

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from obsion.evaluations.acceptance import AcceptanceError, FrozenCase, Score
from obsion.evaluations.semantic import POLICY_SHA256, SemanticScoreRequest


class APIModelScorer:
    def __init__(self, read_json: Callable[..., Awaitable[Any]], *, profile_id: UUID) -> None:
        self.read_json = read_json
        self.profile_id = profile_id
        self.manifest = {
            "kind": "independent_model",
            "profile_id": str(profile_id),
            "policy_sha256": POLICY_SHA256,
        }

    async def score(self, *, case: FrozenCase, answer: str, source: str, run_id: str) -> Score:
        if hashlib.sha256(source.encode()).hexdigest() != case.source_sha256:
            raise AcceptanceError("independent_source_mismatch")
        request = SemanticScoreRequest(
            run_id=UUID(run_id),
            case=case,
            answer_sha256=hashlib.sha256(answer.encode()).hexdigest(),
            model_profile_id=self.profile_id,
            policy_sha256=POLICY_SHA256,
        )
        result = Score.model_validate(
            await self.read_json(
                "POST",
                "/api/v1/admin/evaluations/answer-score",
                json=request.model_dump(mode="json"),
                timeout=60,
            )
        )
        if result.scorer_id != f"obsion-semantic-v1:{self.profile_id}:{POLICY_SHA256}":
            raise AcceptanceError("independent_scorer_identity_mismatch")
        return result
