"""In-process Connector SDK runtime hosted by the Capability Gateway.

Authors implement ``obsion_sdk.connector.ConnectorAdapter``. This runtime registers
those adapters by connector type, wraps ``execute`` with timeout-budgeted retry,
metrics, and tracing, and exposes operator ``health`` / ``discover`` without
auto-binding Capabilities. Auth, Policy, Audit, schema validation, and Evidence
remain on the Gateway. Remote loading, pip install, and dynamic import are not
implemented.
"""

from __future__ import annotations

from collections.abc import Mapping
from time import perf_counter
from typing import Any

from obsion.capabilities.connectors import ConnectorContext, ConnectorResult, InternalHandler
from obsion.capabilities.plugin_governance import enforce_plugin_governance
from obsion.common.errors import ObsionError, ValidationError
from obsion.common.time import utc_now
from obsion.db.models import Connector
from obsion.telemetry import connector_spi_counter, connector_spi_duration, tracer
from obsion_sdk.connector import (
    ConnectorAdapter,
    ConnectorExecuteContext,
    ConnectorExecuteRequest,
    ConnectorInvocationContext,
    ConnectorSdkError,
    DevelopmentEchoConnector,
    assert_no_forbidden_fields,
    discovery_as_dict,
    health_as_dict,
    parse_execute_request,
)

DEVELOPMENT_CONNECTOR_TYPE = DevelopmentEchoConnector.CONNECTOR_TYPE
DEVELOPMENT_OPERATION = DevelopmentEchoConnector.OPERATION
DEVELOPMENT_CAPABILITY = DevelopmentEchoConnector.CAPABILITY
REMOTE_UNAVAILABLE_MESSAGE = "Connector SDK remote loading and package install are not implemented"
DEFAULT_RETRY_MAX = 2

_REMOTE_CONFIG_KEYS = frozenset(
    {
        "args",
        "base_url",
        "baseurl",
        "class_name",
        "classname",
        "command",
        "entrypoint",
        "import",
        "module",
        "package",
        "pip",
        "pythonpath",
        "url",
        "wheel",
    }
)


class ConnectorSdkRuntime:
    def __init__(self) -> None:
        self._adapters: dict[str, ConnectorAdapter] = {}

    def register(self, connector_type: str, adapter: ConnectorAdapter) -> None:
        if connector_type in self._adapters:
            raise ValueError(f"Connector SDK adapter already registered: {connector_type}")
        self._adapters[connector_type] = adapter

    def supports(self, connector_type: str) -> bool:
        return connector_type in self._adapters

    def as_internal_handler(self) -> InternalHandler:
        async def handler(
            payload: dict[str, Any],
            connector: Connector,
            context: ConnectorContext,
        ) -> ConnectorResult:
            return await self.execute(connector, payload, context.credential, context)

        return handler

    def _require_adapter(self, connector: Connector) -> ConnectorAdapter:
        adapter = self._adapters.get(connector.connector_type)
        if adapter is None:
            raise ValidationError(
                "connector_handler_missing",
                "No Connector SDK adapter is registered for this connector type",
                connector_type=connector.connector_type,
            )
        return adapter

    def _guard_in_process(self, connector: Connector) -> None:
        if connector.endpoint or connector.allowed_egress:
            raise ObsionError("capability_transport_unavailable", REMOTE_UNAVAILABLE_MESSAGE)
        configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
        config_keys = {str(key).casefold() for key in configuration}
        if config_keys & _REMOTE_CONFIG_KEYS:
            raise ObsionError("capability_transport_unavailable", REMOTE_UNAVAILABLE_MESSAGE)

    def _public_context(
        self,
        connector: Connector,
        *,
        operation: str | None = None,
        run_id: str | None = None,
    ) -> ConnectorInvocationContext:
        return ConnectorInvocationContext(
            connector_name=connector.name,
            connector_type=connector.connector_type,
            environment=connector.environment,
            operation=operation,
            run_id=run_id,
        )

    async def probe_health(self, connector: Connector) -> dict[str, Any]:
        with tracer.start_as_current_span("obsion.connector.spi.health") as span:
            span.set_attribute("obsion.connector.type", connector.connector_type)
            started = perf_counter()
            status = "FAILED"
            try:
                self._guard_in_process(connector)
                adapter = self._require_adapter(connector)
                enforce_plugin_governance(connector)
                health = await adapter.health(self._public_context(connector))
                payload = health_as_dict(health, checked_at=utc_now())
                assert_no_forbidden_fields(payload)
                status = "SUCCESS"
                return payload
            except ConnectorSdkError as exc:
                if exc.code == "capability_output_invalid":
                    raise ValidationError("capability_output_invalid", exc.message) from exc
                raise ValidationError("capability_input_invalid", exc.message) from exc
            finally:
                attributes = {"method": "health", "status": status}
                connector_spi_counter.add(1, attributes)
                connector_spi_duration.record((perf_counter() - started) * 1000, attributes)

    async def discover(self, connector: Connector) -> dict[str, Any]:
        with tracer.start_as_current_span("obsion.connector.spi.discover") as span:
            span.set_attribute("obsion.connector.type", connector.connector_type)
            started = perf_counter()
            status = "FAILED"
            try:
                self._guard_in_process(connector)
                adapter = self._require_adapter(connector)
                enforce_plugin_governance(connector)
                discovery = await adapter.discover(self._public_context(connector))
                payload = discovery_as_dict(discovery)
                assert_no_forbidden_fields(payload)
                status = "SUCCESS"
                return payload
            except ConnectorSdkError as exc:
                if exc.code == "capability_output_invalid":
                    raise ValidationError("capability_output_invalid", exc.message) from exc
                raise ValidationError("capability_input_invalid", exc.message) from exc
            finally:
                attributes = {"method": "discover", "status": status}
                connector_spi_counter.add(1, attributes)
                connector_spi_duration.record((perf_counter() - started) * 1000, attributes)

    async def execute(
        self,
        connector: Connector,
        payload: dict[str, Any],
        credential: str | None,
        context: ConnectorContext,
    ) -> ConnectorResult:
        with tracer.start_as_current_span("obsion.connector.spi.execute") as span:
            span.set_attribute("obsion.connector.type", connector.connector_type)
            started = perf_counter()
            status = "FAILED"
            operation = "echo"
            try:
                self._guard_in_process(connector)
                adapter = self._require_adapter(connector)
                enforce_plugin_governance(connector)
                request = parse_execute_request(payload, default_operation=DEVELOPMENT_OPERATION)
                operation = request.operation
                execute_context = ConnectorExecuteContext(
                    connector_name=connector.name,
                    connector_type=connector.connector_type,
                    environment=connector.environment,
                    operation=request.operation,
                    run_id=str(context.run_id),
                    credential=credential,
                )
                data = await self._execute_with_retry(
                    adapter,
                    request,
                    execute_context,
                    connector,
                )
                try:
                    assert_no_forbidden_fields(data, credential=credential)
                except ConnectorSdkError as exc:
                    raise ValidationError("capability_output_invalid", exc.message) from exc
                status = "SUCCESS"
                return ConnectorResult(
                    data=dict(data),
                    source=connector.name,
                    resource=f"connector-sdk://{connector.name}/{request.operation}",
                    observed_at=utc_now(),
                )
            except ConnectorSdkError as exc:
                if exc.code == "capability_output_invalid":
                    raise ValidationError("capability_output_invalid", exc.message) from exc
                raise ValidationError("capability_input_invalid", exc.message) from exc
            finally:
                attributes = {"method": "execute", "status": status, "operation": operation}
                connector_spi_counter.add(1, attributes)
                connector_spi_duration.record((perf_counter() - started) * 1000, attributes)

    async def _execute_with_retry(
        self,
        adapter: ConnectorAdapter,
        request: ConnectorExecuteRequest,
        execute_context: ConnectorExecuteContext,
        connector: Connector,
    ) -> Mapping[str, Any]:
        configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
        side_effect = configuration.get("side_effect") is True
        configured = configuration.get("retry_max", DEFAULT_RETRY_MAX)
        retry_max = (
            configured
            if isinstance(configured, int) and not isinstance(configured, bool) and configured >= 0
            else DEFAULT_RETRY_MAX
        )
        attempts = 1 if side_effect else 1 + retry_max
        last_error: OSError | TimeoutError | None = None
        for _ in range(attempts):
            try:
                return await adapter.execute(request, execute_context)
            except ConnectorSdkError:
                raise
            except (TimeoutError, OSError) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error
