from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

if TYPE_CHECKING:
    from obsion.config import Settings
    from obsion.db.session import Database

_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None

tracer = trace.get_tracer("obsion.control-plane")
meter = metrics.get_meter("obsion.control-plane")
critic_coverage = meter.create_histogram("obsion.critic.evidence_coverage", unit="1")
run_counter = meter.create_counter("obsion.runs", unit="run")
run_duration = meter.create_histogram("obsion.run.duration", unit="ms")
capability_counter = meter.create_counter("obsion.capability.invocations", unit="invocation")
capability_duration = meter.create_histogram("obsion.capability.duration", unit="ms")
policy_counter = meter.create_counter("obsion.policy.decisions", unit="decision")
policy_duration = meter.create_histogram("obsion.policy.duration", unit="ms")
approval_counter = meter.create_counter("obsion.approval.decisions", unit="decision")
model_counter = meter.create_counter("obsion.model.calls", unit="call")
model_duration = meter.create_histogram("obsion.model.duration", unit="ms")
model_tokens = meter.create_counter("obsion.model.tokens", unit="token")
model_cost = meter.create_histogram("obsion.model.cost", unit="1")
replan_counter = meter.create_counter("obsion.run.replans", unit="replan")
run_ttft = meter.create_histogram("obsion.run.ttft", unit="ms")
run_steps = meter.create_histogram("obsion.run.steps", unit="1")
sql_duration = meter.create_histogram("obsion.sql.duration", unit="ms")
connector_spi_counter = meter.create_counter("obsion.connector.spi", unit="operation")
connector_spi_duration = meter.create_histogram("obsion.connector.spi.duration", unit="ms")
connector_plugin_counter = meter.create_counter("obsion.connector.plugin", unit="operation")
retrieval_duration = meter.create_histogram("obsion.retrieval.duration", unit="ms")
automation_counter = meter.create_counter("obsion.automation.executions", unit="execution")
automation_duration = meter.create_histogram("obsion.automation.duration", unit="ms")
action_counter = meter.create_counter("obsion.action.attempts", unit="attempt")
evaluation_counter = meter.create_counter("obsion.evaluation.runs", unit="evaluation")
studio_counter = meter.create_counter("obsion.studio.operations", unit="operation")
evaluation_case_duration = meter.create_histogram("obsion.evaluation.case.duration", unit="ms")
memory_context_counter = meter.create_counter("obsion.memory.context", unit="memory")
conversation_context_counter = meter.create_counter("obsion.conversation.context", unit="turn")
context_budget_counter = meter.create_counter("obsion.context.budget", unit="decision")
conversation_compact_counter = meter.create_counter(
    "obsion.conversation.compact", unit="compaction"
)
workspace_context_counter = meter.create_counter("obsion.workspace.context", unit="workspace")
run_satisfaction = meter.create_counter("obsion.run.satisfaction", unit="feedback")
app_server_connection_counter = meter.create_counter(
    "obsion.app_server.connections", unit="connection"
)
app_server_request_counter = meter.create_counter("obsion.app_server.requests", unit="request")
app_server_event_counter = meter.create_counter("obsion.app_server.events", unit="event")


def configure_telemetry(app: FastAPI, settings: Settings) -> None:
    global _meter_provider, _provider
    if not settings.otel_enabled:
        return
    if _provider is None:
        resource = Resource.create(
            {
                "service.name": settings.otel_service_name,
                "service.version": app.version,
                "deployment.environment.name": settings.environment.value,
            }
        )
        _provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(TraceIdRatioBased(settings.otel_trace_sample_ratio)),
        )
        endpoint = settings.otel_exporter_otlp_endpoint
        if endpoint is not None:
            headers = _parse_headers(
                settings.otel_exporter_headers.get_secret_value()
                if settings.otel_exporter_headers is not None
                else ""
            )
            exporter = OTLPSpanExporter(
                endpoint=_signal_endpoint(str(endpoint), "traces"), headers=headers
            )
            _provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(_provider)
    if _meter_provider is None:
        metric_readers = []
        endpoint = settings.otel_exporter_otlp_endpoint
        if endpoint is not None:
            headers = _parse_headers(
                settings.otel_exporter_headers.get_secret_value()
                if settings.otel_exporter_headers is not None
                else ""
            )
            metric_readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(
                        endpoint=_signal_endpoint(str(endpoint), "metrics"), headers=headers
                    )
                )
            )
        _meter_provider = MeterProvider(
            resource=Resource.create(
                {
                    "service.name": settings.otel_service_name,
                    "deployment.environment.name": settings.environment.value,
                }
            ),
            metric_readers=metric_readers,
        )
        metrics.set_meter_provider(_meter_provider)
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=_provider,
        excluded_urls="/health/live,/health/ready",
    )


def instrument_database(database: Database) -> None:
    if _provider is None:
        return
    SQLAlchemyInstrumentor().instrument(
        engine=database.engine.sync_engine,
        tracer_provider=_provider,
        enable_commenter=False,
    )


def flush_telemetry() -> None:
    if _provider is not None:
        _provider.force_flush(timeout_millis=5000)
    if _meter_provider is not None:
        _meter_provider.force_flush(timeout_millis=5000)


def _parse_headers(value: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for pair in value.split(","):
        key, separator, item = pair.partition("=")
        if separator and key.strip():
            headers[key.strip()] = item.strip()
    return headers


def _signal_endpoint(base: str, signal: str) -> str:
    normalized = base.rstrip("/")
    suffix = f"/v1/{signal}"
    if normalized.endswith(suffix):
        return normalized
    return f"{normalized}{suffix}"
