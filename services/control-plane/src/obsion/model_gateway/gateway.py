import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from time import perf_counter
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.capabilities.connectors import CredentialBroker
from obsion.common.errors import BudgetExceededError, ObsionError
from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.config import Environment, Settings
from obsion.db.models import ModelCall, ModelEndpoint, ModelProfile, ModelProfileEndpoint
from obsion.domain.enums import Classification
from obsion.security.egress import validate_model_endpoint
from obsion.security.redaction import redact
from obsion.telemetry import model_counter, tracer


@dataclass(frozen=True, slots=True)
class ModelResult:
    content: str
    endpoint_id: UUID
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_amount: Decimal
    finish_reason: str | None


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    embeddings: list[list[float]]
    endpoint_id: UUID
    input_tokens: int
    latency_ms: int
    cost_amount: Decimal


class ModelUnavailableError(ObsionError):
    def __init__(self, message: str = "No eligible model endpoint is configured") -> None:
        super().__init__("model_unavailable", message, status_code=503)


class ModelGateway:
    embedding_dimensions = 1536

    def __init__(
        self,
        settings: Settings,
        credentials: CredentialBroker | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.credentials = credentials or CredentialBroker()
        self.transport = transport

    async def complete(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID,
        run_id: UUID,
        step_id: UUID | None,
        profile_id: UUID,
        messages: list[dict[str, str]],
        classification: Classification,
        json_mode: bool = False,
        temperature: float = 0.1,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
        max_cost_amount: Decimal | None = None,
    ) -> ModelResult:
        with tracer.start_as_current_span("obsion.model.complete") as span:
            span.set_attribute("obsion.model.profile_id", str(profile_id))
            span.set_attribute("obsion.run.id", str(run_id))
            try:
                result = await self._complete(
                    session,
                    organization_id=organization_id,
                    run_id=run_id,
                    step_id=step_id,
                    profile_id=profile_id,
                    messages=messages,
                    classification=classification,
                    json_mode=json_mode,
                    temperature=temperature,
                    max_input_tokens=max_input_tokens,
                    max_output_tokens=max_output_tokens,
                    max_cost_amount=max_cost_amount,
                )
            except Exception as exc:
                span.record_exception(exc)
                model_counter.add(1, {"status": "FAILED"})
                raise
            span.set_attribute("obsion.model.endpoint_id", str(result.endpoint_id))
            span.set_attribute("obsion.model.input_tokens", result.input_tokens)
            span.set_attribute("obsion.model.output_tokens", result.output_tokens)
            model_counter.add(1, {"status": "SUCCESS"})
            return result

    async def _complete(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID,
        run_id: UUID,
        step_id: UUID | None,
        profile_id: UUID,
        messages: list[dict[str, str]],
        classification: Classification,
        json_mode: bool = False,
        temperature: float = 0.1,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
        max_cost_amount: Decimal | None = None,
    ) -> ModelResult:
        profile, endpoint = await self._route(
            session,
            organization_id,
            profile_id,
            classification,
            required_capability="chat",
        )
        validate_model_endpoint(
            endpoint.base_url,
            self.settings.model_allowed_hosts,
            allow_insecure_loopback=self.settings.environment
            in {Environment.DEVELOPMENT, Environment.TEST},
        )
        safe_messages = redact(messages)
        estimated_input_tokens = max(
            1,
            len(json.dumps(safe_messages, ensure_ascii=False, separators=(",", ":"))),
        )
        if max_input_tokens is not None and estimated_input_tokens > max_input_tokens:
            raise BudgetExceededError("input_tokens", max_input_tokens)
        endpoint_output_limit = int(endpoint.limits.get("max_output_tokens", 16_000))
        output_limit = min(max_output_tokens or endpoint_output_limit, endpoint_output_limit)
        pricing = endpoint.limits.get("pricing_per_million", {})
        estimated_cost = (
            Decimal(estimated_input_tokens) * Decimal(str(pricing.get("input", 0)))
            + Decimal(output_limit) * Decimal(str(pricing.get("output", 0)))
        ) / Decimal(1_000_000)
        if max_cost_amount is not None and estimated_cost > max_cost_amount:
            raise BudgetExceededError("cost_amount", max_cost_amount)
        request_fingerprint = hashlib.sha256(
            json.dumps(safe_messages, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        credential = await self.credentials.resolve(
            endpoint.credential_ref,
            session=session,
            organization_id=organization_id,
        )
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        payload: dict[str, Any] = {
            "model": endpoint.model_id,
            "messages": safe_messages,
            "temperature": temperature,
            "stream": False,
            "max_tokens": output_limit,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = perf_counter()
        outcome = "SUCCESS"
        input_tokens = 0
        output_tokens = 0
        cost = Decimal("0")
        try:
            url = f"{endpoint.base_url.rstrip('/')}/chat/completions"
            async with httpx.AsyncClient(
                timeout=self.settings.model_request_timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
            choice = body["choices"][0]
            content = choice["message"].get("content") or ""
            usage = body.get("usage", {})
            input_tokens = int(usage.get("prompt_tokens", 0))
            output_tokens = int(usage.get("completion_tokens", 0))
            cost = (
                Decimal(input_tokens) * Decimal(str(pricing.get("input", 0)))
                + Decimal(output_tokens) * Decimal(str(pricing.get("output", 0)))
            ) / Decimal(1_000_000)
            finish_reason = choice.get("finish_reason")
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            outcome = "FAILED"
            raise ModelUnavailableError(
                "The selected model endpoint could not complete the request"
            ) from exc
        finally:
            credential = None
            latency_ms = int((perf_counter() - started) * 1000)
            call = ModelCall(
                id=new_id(),
                organization_id=organization_id,
                run_id=run_id,
                step_id=step_id,
                operation="CHAT",
                profile_id=profile.id,
                endpoint_id=endpoint.id,
                request_fingerprint=request_fingerprint,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                cost_amount=cost,
                outcome=outcome,
                created_at=utc_now(),
            )
            session.add(call)
            await session.flush()
        return ModelResult(
            content=content,
            endpoint_id=endpoint.id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_amount=cost,
            finish_reason=finish_reason,
        )

    async def embed(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID,
        profile_name: str,
        texts: list[str],
        classification: Classification,
    ) -> EmbeddingResult:
        if not texts:
            raise ValueError("texts must not be empty")
        with tracer.start_as_current_span("obsion.model.embed") as span:
            span.set_attribute("obsion.model.profile_name", profile_name)
            span.set_attribute("obsion.model.embedding_count", len(texts))
            try:
                result = await self._embed(
                    session,
                    organization_id=organization_id,
                    profile_name=profile_name,
                    texts=texts,
                    classification=classification,
                )
            except Exception as exc:
                span.record_exception(exc)
                model_counter.add(1, {"operation": "EMBEDDING", "status": "FAILED"})
                raise
            span.set_attribute("obsion.model.endpoint_id", str(result.endpoint_id))
            model_counter.add(1, {"operation": "EMBEDDING", "status": "SUCCESS"})
            return result

    async def _embed(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID,
        profile_name: str,
        texts: list[str],
        classification: Classification,
    ) -> EmbeddingResult:
        profile, endpoint = await self._route_by_name(
            session,
            organization_id,
            profile_name,
            classification,
            required_capability="embeddings",
        )
        validate_model_endpoint(
            endpoint.base_url,
            self.settings.model_allowed_hosts,
            allow_insecure_loopback=self.settings.environment
            in {Environment.DEVELOPMENT, Environment.TEST},
        )
        safe_texts = [str(item) for item in redact(texts)]
        request_fingerprint = hashlib.sha256(
            json.dumps(safe_texts, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        credential = await self.credentials.resolve(
            endpoint.credential_ref,
            session=session,
            organization_id=organization_id,
        )
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        payload = {"model": endpoint.model_id, "input": safe_texts, "encoding_format": "float"}
        started = perf_counter()
        outcome = "SUCCESS"
        input_tokens = 0
        cost = Decimal("0")
        embeddings: list[list[float]] = []
        try:
            url = f"{endpoint.base_url.rstrip('/')}/embeddings"
            async with httpx.AsyncClient(
                timeout=self.settings.model_request_timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
            indexed = sorted(body["data"], key=lambda item: int(item["index"]))
            embeddings = [[float(value) for value in item["embedding"]] for item in indexed]
            if len(embeddings) != len(texts) or any(
                len(vector) != self.embedding_dimensions for vector in embeddings
            ):
                raise ValueError("embedding response has an incompatible shape")
            usage = body.get("usage", {})
            input_tokens = int(usage.get("prompt_tokens", usage.get("total_tokens", 0)))
            pricing = endpoint.limits.get("pricing_per_million", {})
            cost = (
                Decimal(input_tokens)
                * Decimal(str(pricing.get("embedding", pricing.get("input", 0))))
                / Decimal(1_000_000)
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            outcome = "FAILED"
            raise ModelUnavailableError(
                "The selected embedding endpoint could not complete the request"
            ) from exc
        finally:
            credential = None
            latency_ms = int((perf_counter() - started) * 1000)
            session.add(
                ModelCall(
                    id=new_id(),
                    organization_id=organization_id,
                    run_id=None,
                    step_id=None,
                    operation="EMBEDDING",
                    profile_id=profile.id,
                    endpoint_id=endpoint.id,
                    request_fingerprint=request_fingerprint,
                    input_tokens=input_tokens,
                    output_tokens=0,
                    latency_ms=latency_ms,
                    cost_amount=cost,
                    outcome=outcome,
                    created_at=utc_now(),
                )
            )
            await session.flush()
        return EmbeddingResult(
            embeddings=embeddings,
            endpoint_id=endpoint.id,
            input_tokens=input_tokens,
            latency_ms=latency_ms,
            cost_amount=cost,
        )

    async def _route(
        self,
        session: AsyncSession,
        organization_id: UUID,
        profile_id: UUID,
        classification: Classification,
        *,
        required_capability: str,
    ) -> tuple[ModelProfile, ModelEndpoint]:
        rows = (
            await session.execute(
                select(ModelProfile, ModelEndpoint)
                .join(ModelProfileEndpoint, ModelProfileEndpoint.profile_id == ModelProfile.id)
                .join(ModelEndpoint, ModelEndpoint.id == ModelProfileEndpoint.endpoint_id)
                .where(
                    ModelProfile.id == profile_id,
                    ModelProfile.organization_id == organization_id,
                    ModelProfile.enabled.is_(True),
                    ModelEndpoint.organization_id == organization_id,
                    ModelEndpoint.enabled.is_(True),
                )
                .order_by(ModelProfileEndpoint.priority.asc(), ModelEndpoint.created_at.asc())
            )
        ).all()
        for profile, endpoint in rows:
            allowed = set(endpoint.classifications)
            capabilities = set(endpoint.capabilities)
            requirements = profile.requirements
            required_capabilities = set(requirements.get("capabilities", []))
            required_region = requirements.get("region")
            minimum_context = int(requirements.get("min_context_window", 0))
            endpoint_context = int(endpoint.limits.get("context_window", 0))
            if classification.value not in allowed or required_capability not in capabilities:
                continue
            if required_capabilities - capabilities:
                continue
            if required_region and endpoint.region != required_region:
                continue
            if minimum_context and endpoint_context < minimum_context:
                continue
            return profile, endpoint
        raise ModelUnavailableError()

    async def _route_by_name(
        self,
        session: AsyncSession,
        organization_id: UUID,
        profile_name: str,
        classification: Classification,
        *,
        required_capability: str,
    ) -> tuple[ModelProfile, ModelEndpoint]:
        rows = (
            await session.execute(
                select(ModelProfile, ModelEndpoint)
                .join(ModelProfileEndpoint, ModelProfileEndpoint.profile_id == ModelProfile.id)
                .join(ModelEndpoint, ModelEndpoint.id == ModelProfileEndpoint.endpoint_id)
                .where(
                    ModelProfile.name == profile_name,
                    ModelProfile.organization_id == organization_id,
                    ModelProfile.enabled.is_(True),
                    ModelEndpoint.organization_id == organization_id,
                    ModelEndpoint.enabled.is_(True),
                )
                .order_by(ModelProfileEndpoint.priority.asc(), ModelEndpoint.created_at.asc())
            )
        ).all()
        for profile, endpoint in rows:
            allowed_classifications = set(endpoint.classifications)
            capabilities = set(endpoint.capabilities)
            if (
                classification.value in allowed_classifications
                and required_capability in capabilities
            ):
                return profile, endpoint
        raise ModelUnavailableError(
            f"No eligible {required_capability} endpoint is configured for profile {profile_name}"
        )
