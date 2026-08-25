from fastapi import FastAPI
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from obsion import telemetry
from obsion.config import Environment, Settings


def test_manual_spans_can_be_exported_and_signal_endpoints_are_normalized() -> None:
    app = FastAPI(version="test")
    telemetry.configure_telemetry(
        app,
        Settings(_env_file=None, environment=Environment.TEST, otel_enabled=True),
    )
    assert telemetry._provider is not None
    exporter = InMemorySpanExporter()
    telemetry._provider.add_span_processor(SimpleSpanProcessor(exporter))

    with telemetry.tracer.start_as_current_span("obsion.test") as span:
        span.set_attribute("obsion.test", True)
    telemetry._provider.force_flush()

    assert any(span.name == "obsion.test" for span in exporter.get_finished_spans())
    assert telemetry._signal_endpoint("http://collector:4318", "traces") == (
        "http://collector:4318/v1/traces"
    )
    assert telemetry._signal_endpoint("http://collector:4318/v1/metrics", "metrics") == (
        "http://collector:4318/v1/metrics"
    )
