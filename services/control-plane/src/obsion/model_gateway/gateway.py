import hashlib
import json
from collections.abc import Mapping
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
from obsion.model_gateway.providers import (
    ModelProviderAdapter,
    ModelTool,
    ModelToolCall,
    ProviderCompletionRequest,
    ProviderProtocolError,
    builtin_provider_adapters,
    validate_tool_calls,
    validate_tools,
)
from obsion.security.egress import validate_model_endpoint
from obsion.security.redaction import redact
from obsion.telemetry import model_counter, tracer


@dataclass(frozen=True, slots=True)
class ModelResult:
    content: str
    profile_id: UUID
    endpoint_id: UUID
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_amount: Decimal
    finish_reason: str | None
    tool_calls: tuple[ModelToolCall, ...] = ()


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
        provider_adapters: Mapping[str, ModelProviderAdapter] | None = None,
    ) -> None:
        self.settings = settings
        self.credentials = credentials or CredentialBroker()
        self.transport = transport
        self.provider_adapters = dict(provider_adapters or builtin_provider_adapters())

    async def complete(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID,
        run_id: UUID,
        step_id: UUID | None,
        profile_id: UUID,
        messages: list[dict[str, Any]],
        classification: Classification,
        json_mode: bool = False,
        temperature: float = 0.1,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
        max_cost_amount: Decimal | None = None,
        tools: tuple[ModelTool, ...] = (),
        tool_choice: str | None = None,
    ) -> ModelResult:
        if not messages:
            raise ValueError("messages must not be empty")
        if not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if max_input_tokens is not None and max_input_tokens <= 0:
            raise ValueError("max_input_tokens must be positive")
        if max_output_tokens is not None and max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if max_cost_amount is not None and max_cost_amount <= 0:
            raise ValueError("max_cost_amount must be positive")
        validate_tools(tools, tool_choice)
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
                    tools=tools,
                    tool_choice=tool_choice,
                )
            except Exception as exc:
                span.record_exception(exc)
                model_counter.add(1, {"status": "FAILED"})
                raise
            span.set_attribute("obsion.model.endpoint_id", str(result.endpoint_id))
            span.set_attribute("obsion.model.effective_profile_id", str(result.profile_id))
            span.set_attribute("obsion.model.input_tokens", result.input_tokens)
            span.set_attribute("obsion.model.output_tokens", result.output_tokens)
            span.set_attribute("obsion.model.tool_call_count", len(result.tool_calls))
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
        messages: list[dict[str, Any]],
        classification: Classification,
        json_mode: bool = False,
        temperature: float = 0.1,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
        max_cost_amount: Decimal | None = None,
        tools: tuple[ModelTool, ...] = (),
        tool_choice: str | None = None,
    ) -> ModelResult:
        required_capabilities = {"chat"}
        if json_mode:
            required_capabilities.add("json_mode")
        if tools:
            required_capabilities.add("tool_call")
        profile, endpoints = await self._routes(
            session,
            organization_id,
            profile_id,
            classification,
            required_capabilities=required_capabilities,
        )
        safe_messages = redact(messages)
        if not isinstance(safe_messages, list):
            raise ValueError("messages must be an array")
        safe_tool_list: list[ModelTool] = []
        for tool in tools:
            safe_schema = redact(tool.input_schema)
            if not isinstance(safe_schema, dict):
                raise ValueError(f"tool input schema is invalid after redaction: {tool.name}")
            safe_tool_list.append(
                ModelTool(
                    name=tool.name,
                    description=str(redact(tool.description)),
                    input_schema=safe_schema,
                )
            )
        safe_tools = tuple(safe_tool_list)
        validate_tools(safe_tools, tool_choice)
        request_contract = {
            "messages": safe_messages,
            "json_mode": json_mode,
            "temperature": temperature,
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in safe_tools
            ],
            "tool_choice": tool_choice,
        }
        serialized_contract = json.dumps(
            request_contract,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        estimated_input_tokens = max(1, (len(serialized_contract) + 3) // 4)
        if max_input_tokens is not None and estimated_input_tokens > max_input_tokens:
            raise BudgetExceededError("input_tokens", max_input_tokens)
        request_fingerprint = hashlib.sha256(serialized_contract.encode()).hexdigest()
        allow_fallback = bool(profile.routing_policy.get("fallback", False))
        candidates = endpoints if allow_fallback else endpoints[:1]
        last_error: Exception | None = None
        for endpoint in candidates:
            try:
                return await self._complete_with_endpoint(
                    session,
                    organization_id=organization_id,
                    run_id=run_id,
                    step_id=step_id,
                    profile=profile,
                    endpoint=endpoint,
                    messages=safe_messages,
                    json_mode=json_mode,
                    temperature=temperature,
                    estimated_input_tokens=estimated_input_tokens,
                    max_output_tokens=max_output_tokens,
                    max_cost_amount=max_cost_amount,
                    request_fingerprint=request_fingerprint,
                    tools=safe_tools,
                    tool_choice=tool_choice,
                )
            except ModelUnavailableError as exc:
                last_error = exc
                if not allow_fallback:
                    raise
        raise ModelUnavailableError(
            "All eligible model endpoints failed for the selected profile"
        ) from last_error

    async def _complete_with_endpoint(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID,
        run_id: UUID,
        step_id: UUID | None,
        profile: ModelProfile,
        endpoint: ModelEndpoint,
        messages: list[dict[str, Any]],
        json_mode: bool,
        temperature: float,
        estimated_input_tokens: int,
        max_output_tokens: int | None,
        max_cost_amount: Decimal | None,
        request_fingerprint: str,
        tools: tuple[ModelTool, ...],
        tool_choice: str | None,
    ) -> ModelResult:
        validate_model_endpoint(
            endpoint.base_url,
            self.settings.model_allowed_hosts,
            allow_insecure_loopback=self.settings.environment
            in {Environment.DEVELOPMENT, Environment.TEST},
        )
        adapter = self.provider_adapters.get(endpoint.provider.casefold())
        if adapter is None:
            raise ModelUnavailableError(
                f"Model provider protocol is not supported: {endpoint.provider}"
            )
        endpoint_output_limit = int(endpoint.limits.get("max_output_tokens", 16_000))
        output_limit = min(max_output_tokens or endpoint_output_limit, endpoint_output_limit)
        pricing = endpoint.limits.get("pricing_per_million", {})
        estimated_cost = (
            Decimal(estimated_input_tokens) * Decimal(str(pricing.get("input", 0)))
            + Decimal(output_limit) * Decimal(str(pricing.get("output", 0)))
        ) / Decimal(1_000_000)
        if max_cost_amount is not None and estimated_cost > max_cost_amount:
            raise BudgetExceededError("cost_amount", max_cost_amount)
        credential = await self.credentials.resolve(
            endpoint.credential_ref,
            session=session,
            organization_id=organization_id,
        )
        provider_request = adapter.build_completion_request(
            ProviderCompletionRequest(
                model_id=endpoint.model_id,
                messages=messages,
                temperature=temperature,
                max_output_tokens=output_limit,
                json_mode=json_mode,
                tools=tools,
                tool_choice=tool_choice,
            ),
            credential=credential,
        )
        started = perf_counter()
        outcome = "SUCCESS"
        input_tokens = 0
        output_tokens = 0
        cost = Decimal("0")
        try:
            url = f"{endpoint.base_url.rstrip('/')}/{provider_request.path.lstrip('/')}"
            async with httpx.AsyncClient(
                timeout=self.settings.model_request_timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    url,
                    headers=provider_request.headers,
                    json=provider_request.payload,
                )
                response.raise_for_status()
            completion = adapter.parse_completion_response(response)
            input_tokens = completion.input_tokens
            output_tokens = completion.output_tokens
            cost = (
                Decimal(input_tokens) * Decimal(str(pricing.get("input", 0)))
                + Decimal(output_tokens) * Decimal(str(pricing.get("output", 0)))
            ) / Decimal(1_000_000)
            validate_tool_calls(completion.tool_calls, tools, tool_choice)
            if json_mode and not completion.tool_calls:
                parsed = json.loads(completion.content)
                if not isinstance(parsed, dict):
                    raise ProviderProtocolError("JSON mode must return a JSON object")
        except (httpx.HTTPError, ProviderProtocolError, json.JSONDecodeError) as exc:
            outcome = "FAILED"
            raise ModelUnavailableError(
                "The selected model endpoint could not complete the request"
            ) from exc
        finally:
            credential = None
            latency_ms = int((perf_counter() - started) * 1000)
            session.add(
                ModelCall(
                    id=new_id(),
                    organization_id=organization_id,
                    run_id=run_id,
                    step_id=step_id,
                    operation="TOOL_CALL" if tools else "CHAT",
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
            )
            await session.flush()
        return ModelResult(
            content=completion.content,
            profile_id=profile.id,
            endpoint_id=endpoint.id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_amount=cost,
            finish_reason=completion.finish_reason,
            tool_calls=completion.tool_calls,
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

    async def _routes(
        self,
        session: AsyncSession,
        organization_id: UUID,
        profile_id: UUID,
        classification: Classification,
        *,
        required_capabilities: set[str],
    ) -> tuple[ModelProfile, list[ModelEndpoint]]:
        requested_profile = await session.scalar(
            select(ModelProfile).where(
                ModelProfile.id == profile_id,
                ModelProfile.organization_id == organization_id,
                ModelProfile.enabled.is_(True),
            )
        )
        if requested_profile is None:
            raise ModelUnavailableError()
        profile = await self._effective_profile(
            session,
            organization_id,
            requested_profile,
            classification,
        )
        endpoints = await self._eligible_endpoints(
            session,
            organization_id,
            profile,
            classification,
            required_capabilities,
        )
        if not endpoints:
            raise ModelUnavailableError()
        return profile, endpoints

    async def _route_by_name(
        self,
        session: AsyncSession,
        organization_id: UUID,
        profile_name: str,
        classification: Classification,
        *,
        required_capability: str,
    ) -> tuple[ModelProfile, ModelEndpoint]:
        requested_profile = await session.scalar(
            select(ModelProfile).where(
                ModelProfile.name == profile_name,
                ModelProfile.organization_id == organization_id,
                ModelProfile.enabled.is_(True),
            )
        )
        if requested_profile is None:
            raise ModelUnavailableError(
                "No eligible "
                f"{required_capability} endpoint is configured for profile {profile_name}"
            )
        profile = await self._effective_profile(
            session,
            organization_id,
            requested_profile,
            classification,
        )
        endpoints = await self._eligible_endpoints(
            session,
            organization_id,
            profile,
            classification,
            {required_capability},
        )
        if not endpoints:
            raise ModelUnavailableError(
                "No eligible "
                f"{required_capability} endpoint is configured for profile {profile.name}"
            )
        return profile, endpoints[0]

    async def _effective_profile(
        self,
        session: AsyncSession,
        organization_id: UUID,
        requested_profile: ModelProfile,
        classification: Classification,
    ) -> ModelProfile:
        if not self.settings.model_force_private_for_sensitive or classification not in {
            Classification.CONFIDENTIAL,
            Classification.RESTRICTED,
        }:
            return requested_profile
        if requested_profile.name == self.settings.model_private_profile_name:
            return requested_profile
        private_profile = await session.scalar(
            select(ModelProfile).where(
                ModelProfile.organization_id == organization_id,
                ModelProfile.name == self.settings.model_private_profile_name,
                ModelProfile.enabled.is_(True),
            )
        )
        if private_profile is None:
            raise ModelUnavailableError("Sensitive model input requires an enabled private profile")
        return private_profile

    async def _eligible_endpoints(
        self,
        session: AsyncSession,
        organization_id: UUID,
        profile: ModelProfile,
        classification: Classification,
        request_capabilities: set[str],
    ) -> list[ModelEndpoint]:
        endpoints = (
            await session.scalars(
                select(ModelEndpoint)
                .join(ModelProfileEndpoint, ModelProfileEndpoint.endpoint_id == ModelEndpoint.id)
                .where(
                    ModelProfileEndpoint.profile_id == profile.id,
                    ModelEndpoint.organization_id == organization_id,
                    ModelEndpoint.enabled.is_(True),
                )
                .order_by(ModelProfileEndpoint.priority.asc(), ModelEndpoint.created_at.asc())
            )
        ).all()
        requirements = profile.requirements
        required_capabilities = set(requirements.get("capabilities", [])) | request_capabilities
        required_region = requirements.get("region")
        required_providers = {str(item).casefold() for item in requirements.get("providers", [])}
        minimum_context = int(requirements.get("min_context_window", 0))
        sensitive_private_required = (
            self.settings.model_force_private_for_sensitive
            and classification in {Classification.CONFIDENTIAL, Classification.RESTRICTED}
            and profile.name == self.settings.model_private_profile_name
        )
        if sensitive_private_required and requirements.get("private") is not True:
            return []
        private_only = requirements.get("private") is True
        eligible: list[ModelEndpoint] = []
        for endpoint in endpoints:
            capabilities = set(endpoint.capabilities)
            endpoint_context = int(endpoint.limits.get("context_window", 0))
            if classification.value not in set(endpoint.classifications):
                continue
            if required_capabilities - capabilities:
                continue
            if required_region and endpoint.region != required_region:
                continue
            if required_providers and endpoint.provider.casefold() not in required_providers:
                continue
            if minimum_context and endpoint_context < minimum_context:
                continue
            if private_only and endpoint.limits.get("private") is not True:
                continue
            eligible.append(endpoint)
        return eligible
