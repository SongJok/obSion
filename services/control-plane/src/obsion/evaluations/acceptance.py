"""Frozen acceptance execution through the same HTTP task API used by clients.

This driver never uses the candidate's claims/VERIFIED flags as a quality score.
Independent scorers are a separate contract; missing scoring stays NOT_RUN.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from obsion.common.time import ensure_utc
from obsion.evaluations.engine import canonical_sha256
from obsion.security.redaction import redact

Status = Literal["PASS", "FAIL", "BLOCKED", "NOT_RUN"]
_CONFIG_PATHS = frozenset(
    {
        "/api/v1/admin/models/profiles",
        "/api/v1/admin/models/endpoints",
        "/api/v1/admin/prompts",
        "/api/v1/admin/capabilities",
        "/api/v1/admin/agents",
        "/api/v1/admin/skills",
        "/api/v1/admin/policies",
    }
)
_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class AcceptanceError(ValueError):
    """Sanitized acceptance failure; never include credentials or HTTP bodies."""


class AcceptanceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    name: str = Field(min_length=1, max_length=100)
    phase: Literal["P1"]
    api_base_url: str
    workspace_id: UUID
    dataset: str
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_profile: str = Field(min_length=1, max_length=120)
    model_profile_id: UUID
    agent_version_id: UUID
    configuration_sha256: dict[str, str]
    timeout_seconds: int = Field(default=120, ge=1, le=120)
    poll_seconds: float = Field(default=1, ge=0.01, le=5)

    @field_validator("api_base_url")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or (url.scheme == "http" and url.hostname not in {"127.0.0.1", "::1", "localhost"})
        ):
            raise ValueError("Use an HTTPS API origin, or HTTP loopback, without credentials")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_configuration(self) -> AcceptanceProfile:
        if set(self.configuration_sha256) != _CONFIG_PATHS or not all(
            re.fullmatch(r"[0-9a-f]{64}", value) for value in self.configuration_sha256.values()
        ):
            raise ValueError("Freeze every required runtime configuration endpoint")
        return self


class FrozenCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_-]+$")
    required: Literal[True]
    question: str = Field(min_length=1, max_length=100_000)
    source_document_id: UUID
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_kind: Literal["ANSWER", "INSUFFICIENT"]
    reviewed_source_quotes: list[str] = Field(min_length=1)
    scoring_rules: list[str] = Field(min_length=1)


class Score(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Status
    reason: str = Field(min_length=1)
    scorer_id: str = Field(min_length=1)
    answer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Written by an independent evaluator, never projected from Run verification.
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class IndependentScorer(Protocol):
    async def score(self, *, case: FrozenCase, answer: str, source: str, run_id: str) -> Score: ...


class PendingScorer:
    async def score(self, *, case: FrozenCase, answer: str, source: str, run_id: str) -> Score:
        return Score(
            status="NOT_RUN",
            reason="independent_semantic_scorer_not_configured",
            scorer_id="none",
            answer_sha256=hashlib.sha256(answer.encode()).hexdigest(),
        )


def load_frozen_cases(profile: AcceptanceProfile, root: Path) -> tuple[bytes, list[FrozenCase]]:
    root = root.resolve()
    dataset = (root / profile.dataset).resolve()
    if not dataset.is_relative_to(root):
        raise AcceptanceError("dataset_outside_acceptance_root")
    raw = dataset.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != profile.dataset_sha256:
        raise AcceptanceError("frozen_dataset_digest_mismatch")
    document = json.loads(raw)
    if document.get("phase") != profile.phase or document.get("split") != "held_out":
        raise AcceptanceError("frozen_dataset_phase_or_split_mismatch")
    cases = [FrozenCase.model_validate(item) for item in document["cases"]]
    if not cases or len({item.id for item in cases}) != len(cases):
        raise AcceptanceError("frozen_dataset_empty_or_duplicate_cases")
    sources = {str(item["document_id"]): item["sha256"] for item in document["sources"]}
    if any(sources.get(str(case.source_document_id)) != case.source_sha256 for case in cases):
        raise AcceptanceError("case_source_not_in_frozen_inventory")
    return raw, cases


def write_private_json(path: Path, document: Any) -> None:
    """Atomically checkpoint local evidence with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _initialize_evidence(output: Path, raw: bytes, profile: AcceptanceProfile) -> None:
    try:
        output.mkdir(parents=True, exist_ok=False, mode=0o700)
    except FileExistsError as exc:
        raise AcceptanceError("acceptance_output_already_exists_use_new_directory") from exc
    write_private_json(output / "inputs.json", json.loads(raw))
    write_private_json(output / "profile.json", profile.model_dump(mode="json"))


def load_reconciliation(
    previous: Path, raw: bytes, profile: AcceptanceProfile, candidate: str
) -> tuple[dict[str, Any], str]:
    """Old reports locate tasks, never supply their answers or quality verdicts."""
    report_bytes = (previous / "report.json").read_bytes()
    if len(report_bytes) > _MAX_RESPONSE_BYTES:
        raise AcceptanceError("previous_report_exceeds_acceptance_limit")
    report = json.loads(report_bytes)
    if (
        not isinstance(report, dict)
        or report.get("schema_version") != 2
        or report.get("candidate") != candidate
        or report.get("dataset_sha256") != hashlib.sha256(raw).hexdigest()
        or report.get("profile_sha256") != canonical_sha256(profile.model_dump(mode="json"))
        or json.loads((previous / "inputs.json").read_bytes()) != json.loads(raw)
        or json.loads((previous / "profile.json").read_bytes()) != profile.model_dump(mode="json")
    ):
        raise AcceptanceError("reconciliation_frozen_inputs_mismatch")
    expected = [(item["id"], item["expected_kind"]) for item in json.loads(raw)["cases"]]
    results = report.get("results")
    if (
        not isinstance(results, list)
        or not all(isinstance(item, dict) for item in results)
        or [(item.get("id"), item.get("expected_kind")) for item in results] != expected
    ):
        raise AcceptanceError("reconciliation_case_inventory_mismatch")
    for result in results:
        for key in ("thread_id", "run_id"):
            if result.get(key) and str(UUID(result[key])) != result[key]:
                raise AcceptanceError("reconciliation_task_identity_invalid")
    run_ids = [item["run_id"] for item in results if item.get("run_id")]
    if len(set(run_ids)) != len(run_ids):
        raise AcceptanceError("reconciliation_duplicate_run")
    return dict(report), hashlib.sha256(report_bytes).hexdigest()


class AcceptanceRunner:
    @classmethod
    async def freeze(
        cls,
        client: httpx.AsyncClient,
        root: Path,
        *,
        name: str,
        candidate: str,
        image_digest: str,
        workspace_id: UUID,
        model_profile: str,
        dataset: str,
        dataset_sha256: str,
    ) -> AcceptanceProfile:
        """Observe configured pins and validate gold sources before task creation."""
        provisional = AcceptanceProfile(
            schema_version=1,
            name=name,
            phase="P1",
            api_base_url=str(client.base_url).rstrip("/"),
            workspace_id=workspace_id,
            model_profile=model_profile,
            model_profile_id=UUID(int=0),
            agent_version_id=UUID(int=0),
            image_digest=image_digest,
            dataset=dataset,
            dataset_sha256=dataset_sha256,
            configuration_sha256=dict.fromkeys(_CONFIG_PATHS, "0" * 64),
        )
        _, cases = await asyncio.to_thread(load_frozen_cases, provisional, root)
        probe = cls(client, provisional, candidate=candidate)
        snapshot = {path: await probe._json("GET", path) for path in sorted(_CONFIG_PATHS)}
        models = [
            item
            for item in snapshot["/api/v1/admin/models/profiles"]
            if item.get("name") == model_profile and item.get("enabled") is True
        ]
        agents = [
            item
            for item in snapshot["/api/v1/admin/agents"]
            if item.get("name") == "knowledge-agent" and item.get("status") == "ACTIVE"
        ]
        if len(models) != 1 or len(agents) != 1:
            raise AcceptanceError("acceptance_model_or_agent_unavailable")
        profile = AcceptanceProfile.model_validate(
            {
                **provisional.model_dump(mode="json"),
                "model_profile_id": models[0]["id"],
                "agent_version_id": agents[0]["version_id"],
                "configuration_sha256": {
                    path: canonical_sha256(value) for path, value in snapshot.items()
                },
            }
        )
        probe = cls(client, profile, candidate=candidate)
        await probe._identity()
        for case in cases:
            await probe._source(case)
        await probe._identity()
        return profile

    def __init__(
        self,
        client: httpx.AsyncClient,
        profile: AcceptanceProfile,
        *,
        candidate: str,
        scorer: IndependentScorer | None = None,
        scorer_model_profile: str | None = None,
    ) -> None:
        if not re.fullmatch(r"[0-9a-f]{40}", candidate):
            raise AcceptanceError("candidate_requires_full_commit_sha")
        if str(client.base_url).rstrip("/") != profile.api_base_url:
            raise AcceptanceError("client_origin_differs_from_frozen_profile")
        self.client = client
        self.profile = profile
        self.candidate = candidate
        self.scorer = scorer or PendingScorer()
        self.scorer_model_profile = scorer_model_profile

    async def _resolve_scorer(self) -> None:
        if self.scorer_model_profile is None:
            return
        from obsion.evaluations.remote_scorer import APIModelScorer

        profiles = await self._json("GET", "/api/v1/admin/models/profiles")
        matches = [
            item
            for item in profiles
            if item.get("name") == self.scorer_model_profile and item.get("enabled") is True
        ]
        if len(matches) != 1:
            raise AcceptanceError("independent_model_profile_unavailable")
        self.scorer = APIModelScorer(self._json, profile_id=UUID(matches[0]["id"]))

    async def _read(self, method: str, path: str, **kwargs: Any) -> bytes:
        # Never follow redirects with an acceptance principal's bearer token,
        # retry task creation, or contact a connector/provider directly.
        async with self.client.stream(method, path, follow_redirects=False, **kwargs) as response:
            if not 200 <= response.status_code < 300:
                raise AcceptanceError(f"api_http_{response.status_code}")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > _MAX_RESPONSE_BYTES:
                    raise AcceptanceError("api_response_exceeds_acceptance_limit")
            return bytes(body)

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        return json.loads(await self._read(method, path, **kwargs))

    async def _identity(self) -> dict[str, Any]:
        identity = await self._json("GET", "/api/v1/admin/runtime-identity")
        if (
            identity.get("revision") != self.candidate
            or identity.get("image_digest") != self.profile.image_digest
        ):
            raise AcceptanceError("deployed_candidate_or_image_mismatch")
        for path, digest in sorted(self.profile.configuration_sha256.items()):
            if canonical_sha256(await self._json("GET", path)) != digest:
                raise AcceptanceError("runtime_configuration_drift")
        return dict(identity)

    async def _source(self, case: FrozenCase) -> str:
        raw = await self._read(
            "GET", f"/api/v1/knowledge/documents/{case.source_document_id}/content"
        )
        if hashlib.sha256(raw).hexdigest() != case.source_sha256:
            raise AcceptanceError("frozen_source_drift")
        source = raw.decode("utf-8")
        if any(not quote or quote not in source for quote in case.reviewed_source_quotes):
            raise AcceptanceError("reviewed_quote_not_in_complete_source")
        return source

    async def _case(
        self,
        case: FrozenCase,
        result: dict[str, Any],
        checkpoint: Callable[[], Awaitable[None]],
    ) -> None:
        await self._identity()
        source = await self._source(case)
        result["admission_state"] = "THREAD_REQUESTED"
        await checkpoint()
        thread = await self._json(
            "POST",
            "/api/v1/threads",
            json={"workspace_id": str(self.profile.workspace_id), "title": f"验收 {case.id}"},
        )
        thread_id = str(UUID(thread["id"]))
        result["thread_id"] = thread_id
        result["admission_state"] = "THREAD_OBSERVED"
        await checkpoint()
        requested_at = datetime.now(UTC)
        result["requested_at"] = requested_at.isoformat()
        result["deadline_at"] = (
            requested_at + timedelta(seconds=self.profile.timeout_seconds)
        ).isoformat()
        result["admission_state"] = "RUN_REQUESTED"
        await checkpoint()
        created = await self._json(
            "POST",
            f"/api/v1/threads/{thread_id}/turns",
            # Gold, source quotes and expected answers are evaluator-only.
            json={"input": case.question, "model_profile": self.profile.model_profile},
        )
        run_id = str(UUID(created["run"]["id"]))
        result["run_id"] = run_id
        result["admission_state"] = "RUN_OBSERVED"
        await checkpoint()
        await self._observe(case, result, source, created["run"])

    async def _reconcile_case(
        self,
        case: FrozenCase,
        result: dict[str, Any],
        previous: dict[str, Any],
        checkpoint: Callable[[], Awaitable[None]],
    ) -> None:
        await self._identity()
        source = await self._source(case)
        if not previous.get("run_id") or not previous.get("thread_id"):
            if previous.get("admission_state") in {
                "THREAD_REQUESTED",
                "THREAD_OBSERVED",
                "RUN_REQUESTED",
                "RUN_OBSERVED",
            }:
                result["admission_state"] = previous["admission_state"]
            if previous.get("thread_id"):
                result["thread_id"] = str(UUID(previous["thread_id"]))
            result.update(
                status="BLOCKED" if previous.get("admission_state") else "NOT_RUN",
                reason="task_creation_not_reconciled_no_automatic_resubmission",
            )
            return
        thread_id = str(UUID(previous["thread_id"]))
        run_id = str(UUID(previous["run_id"]))
        requested = datetime.fromisoformat(previous["requested_at"])
        deadline = datetime.fromisoformat(previous["deadline_at"])
        if (
            requested.tzinfo is None
            or deadline.tzinfo is None
            or deadline - requested != timedelta(seconds=self.profile.timeout_seconds)
            or requested > datetime.now(UTC)
        ):
            raise AcceptanceError("reconciliation_deadline_invalid")
        # Validate the complete workspace/thread/turn/run relation through the
        # ordinary permission-checked APIs, before reading an answer or cancelling.
        threads = await self._json("GET", f"/api/v1/workspaces/{self.profile.workspace_id}/threads")
        if not any(item.get("id") == thread_id for item in threads):
            raise AcceptanceError("reconciliation_workspace_mismatch")
        runs = await self._json("GET", f"/api/v1/threads/{thread_id}/runs")
        turns = await self._json("GET", f"/api/v1/threads/{thread_id}/turns")
        matching_runs = [item for item in runs if item.get("id") == run_id]
        if len(matching_runs) != 1:
            raise AcceptanceError("reconciliation_run_mismatch")
        run = matching_runs[0]
        ensure_utc(datetime.fromisoformat(run["created_at"]))
        if run["status"] == "COMPLETED" and not run.get("completed_at"):
            raise AcceptanceError("reconciliation_run_timestamps_invalid")
        matching_turns = [item for item in turns if item.get("id") == run.get("turn_id")]
        if (
            len(matching_turns) != 1
            or matching_turns[0].get("input_text") != redact(case.question)
            or matching_turns[0].get("context_refs") != []
            or matching_turns[0].get("attachment_refs") != []
            or run.get("replay_of_run_id") is not None
        ):
            raise AcceptanceError("reconciliation_task_input_mismatch")
        result.update(
            thread_id=thread_id,
            run_id=run_id,
            requested_at=requested.isoformat(),
            deadline_at=deadline.isoformat(),
            admission_state="RUN_OBSERVED",
        )
        await checkpoint()
        # A previous timeout/intervention is a failed attempt, even if the task
        # later completed. A new observation must not turn it into a quality pass.
        failed_attempt = previous.get("attempt_failure") or previous.get("reason")
        if failed_attempt in {"task_timeout", "unexpected_user_intervention"}:
            result.update(status="FAIL", reason=failed_attempt, run_status=run["status"])
            if run["status"] not in _TERMINAL:
                await self._cancel(result)
            return
        await self._observe(case, result, source, run)

    async def _observe(
        self, case: FrozenCase, result: dict[str, Any], source: str, run: dict[str, Any]
    ) -> None:
        run_id = result["run_id"]
        deadline_at = datetime.fromisoformat(result["deadline_at"])
        if run.get("created_at"):
            deadline_at = min(
                deadline_at,
                ensure_utc(datetime.fromisoformat(run["created_at"]))
                + timedelta(seconds=self.profile.timeout_seconds),
            )
        deadline = monotonic() + (deadline_at - datetime.now(UTC)).total_seconds()
        while run["status"] not in _TERMINAL:
            if run["status"] in {"WAITING_APPROVAL", "WAITING_USER"}:
                result.update(status="FAIL", reason="unexpected_user_intervention")
                await self._cancel(result)
                return
            if monotonic() >= deadline:
                result.update(status="FAIL", reason="task_timeout")
                await self._cancel(result)
                return
            await asyncio.sleep(self.profile.poll_seconds)
            run = await self._json("GET", f"/api/v1/runs/{run_id}")
            if run.get("id") != run_id:
                raise AcceptanceError("observed_run_identity_mismatch")
        result["run_status"] = run["status"]
        result["usage"] = {
            key: run.get(key)
            for key in (
                "step_count",
                "input_tokens",
                "output_tokens",
                "cost_amount",
                "started_at",
                "completed_at",
            )
        }
        if run["status"] != "COMPLETED":
            result.update(status="FAIL", reason="task_did_not_complete")
            return
        if run.get("completed_at"):
            completed = ensure_utc(datetime.fromisoformat(run["completed_at"]))
            if completed > deadline_at:
                result.update(status="FAIL", reason="task_timeout")
                return
        if not run.get("source_content_available", False):
            raise AcceptanceError("published_source_access_denied")
        if run.get("model_profile_id") != str(self.profile.model_profile_id) or run.get(
            "agent_version_id"
        ) != str(self.profile.agent_version_id):
            raise AcceptanceError("run_model_or_agent_pin_mismatch")
        artifacts = await self._json("GET", f"/api/v1/runs/{run_id}/artifacts")
        answers = [
            item
            for item in artifacts
            if item.get("kind") == "TEXT"
            and item.get("superseded_at") is None
            and isinstance((item.get("inline_content") or {}).get("markdown"), str)
        ]
        if len(answers) != 1 or not answers[0]["inline_content"]["markdown"].strip():
            result.update(status="FAIL", reason="missing_or_ambiguous_published_answer")
            return
        answer = answers[0]["inline_content"]["markdown"]
        result["artifact_id"] = str(UUID(answers[0]["id"]))
        result["answer"] = answer
        result["answer_sha256"] = hashlib.sha256(answer.encode()).hexdigest()
        result["answer_export_sha256"] = hashlib.sha256(str(redact(answer)).encode()).hexdigest()
        result["answer_export_redacted"] = redact(answer) != answer
        result["prompt_pins"] = run.get("prompt_pins", [])
        result["steps"] = await self._json("GET", f"/api/v1/runs/{run_id}/steps")
        evidence = await self._json("GET", f"/api/v1/runs/{run_id}/evidence")
        result["evidence_refs"] = [
            {key: item.get(key) for key in ("id", "step_id", "source", "content_fingerprint")}
            for item in evidence
        ]
        # Re-evaluate current source permission and content after task execution.
        await self._source(case)
        await self._identity()
        try:
            score = await self.scorer.score(case=case, answer=answer, source=source, run_id=run_id)
        except Exception as exc:
            raise AcceptanceError("independent_scorer_failed") from exc
        if score.answer_sha256 != result["answer_sha256"]:
            raise AcceptanceError("independent_score_answer_mismatch")
        result.update(status=score.status, reason=score.reason, score=score.model_dump())

    async def _cancel(self, result: dict[str, Any]) -> None:
        try:
            result["cancel_status"] = (
                await self._json("POST", f"/api/v1/runs/{result['run_id']}/cancel")
            )["status"]
        except (AcceptanceError, httpx.HTTPError, ValueError, KeyError, TypeError):
            # Preserve the original failure, but do not claim resource cleanup.
            result["cancel_status"] = "UNKNOWN"

    async def run(
        self, root: Path, output: Path, *, resume_from: Path | None = None
    ) -> dict[str, Any]:
        raw, cases = await asyncio.to_thread(load_frozen_cases, self.profile, root)
        previous = None
        previous_digest = None
        if resume_from is not None:
            previous, previous_digest = await asyncio.to_thread(
                load_reconciliation, resume_from, raw, self.profile, self.candidate
            )
        await asyncio.to_thread(_initialize_evidence, output, raw, self.profile)
        report: dict[str, Any] = {
            "schema_version": 2,
            "phase": self.profile.phase,
            "profile": self.profile.name,
            "candidate": self.candidate,
            "dataset_sha256": hashlib.sha256(raw).hexdigest(),
            "profile_sha256": canonical_sha256(self.profile.model_dump(mode="json")),
            "started_at": datetime.now(UTC).isoformat(),
            "status": "BLOCKED",
            "scope": "FROZEN_KNOWLEDGE_ANSWER_SUBSET",
            "phase_status": "BLOCKED",
            "promotion_eligible": False,
            "scorer": getattr(self.scorer, "manifest", {"kind": "unconfigured_or_injected"}),
            "reconciles_report_sha256": previous_digest,
            "phase_blockers": [
                "complete_phase_cases_and_hard_gates_required",
                "signed_deployment_attestation_required",
                "worker_execution_identity_not_attested",
            ],
            "results": [
                {"id": case.id, "expected_kind": case.expected_kind, "status": "NOT_RUN"}
                for case in cases
            ],
        }
        report_path = output / "report.json"

        if previous is not None:
            for result, prior in zip(report["results"], previous["results"], strict=True):
                # Even failed preflight must preserve known task locations for
                # the next reconciliation. Never inherit prose, scores or PASS.
                result.update(
                    {
                        key: prior[key]
                        for key in (
                            "thread_id",
                            "run_id",
                            "requested_at",
                            "deadline_at",
                            "admission_state",
                        )
                        if key in prior
                    }
                )
                failure = prior.get("attempt_failure") or prior.get("reason")
                if failure in {"task_timeout", "unexpected_user_intervention"}:
                    result["attempt_failure"] = failure

        async def checkpoint() -> None:
            await asyncio.to_thread(write_private_json, report_path, redact(report))

        await checkpoint()
        try:
            report["deployment_identity"] = await self._identity()
            await self._resolve_scorer()
            report["scorer"] = getattr(
                self.scorer, "manifest", {"kind": "unconfigured_or_injected"}
            )
        except AcceptanceError as exc:
            report["blocker"] = str(exc)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            report["blocker"] = "candidate_preflight_failed"
        else:
            for index, (case, result) in enumerate(zip(cases, report["results"], strict=True)):
                try:
                    if previous is None:
                        await self._case(case, result, checkpoint)
                    else:
                        await self._reconcile_case(
                            case, result, previous["results"][index], checkpoint
                        )
                except AcceptanceError as exc:
                    result.update(status="BLOCKED", reason=str(exc))
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    result.update(status="BLOCKED", reason="task_observation_failed")
                await checkpoint()
            try:
                await self._identity()
                for case in cases:
                    await self._source(case)
            except (AcceptanceError, httpx.HTTPError, ValueError, KeyError, TypeError):
                report["blocker"] = "postflight_source_or_configuration_changed"
        counts = {
            status: sum(r["status"] == status for r in report["results"])
            for status in ("PASS", "FAIL", "BLOCKED", "NOT_RUN")
        }
        report["counts"] = counts
        answerable = [r for r in report["results"] if r["expected_kind"] == "ANSWER"]
        insufficient = [r for r in report["results"] if r["expected_kind"] == "INSUFFICIENT"]
        report["answerable_accuracy"] = (
            sum(r["status"] == "PASS" for r in answerable) / len(answerable) if answerable else None
        )
        report["insufficient_accuracy"] = (
            sum(r["status"] == "PASS" for r in insufficient) / len(insufficient)
            if insufficient
            else None
        )
        report["status"] = (
            "BLOCKED"
            if report.get("blocker") or counts["BLOCKED"] or counts["NOT_RUN"]
            else "FAIL"
            if counts["FAIL"]
            else "PASS"
        )
        report["completed_at"] = datetime.now(UTC).isoformat()
        # Reports contain user-visible answers, but not connector secrets or auth headers.
        sanitized = redact(report)
        await asyncio.to_thread(write_private_json, report_path, sanitized)
        return dict(sanitized)
