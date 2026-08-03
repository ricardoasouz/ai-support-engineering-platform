"""Low-cardinality OpenTelemetry metrics for the application and worker."""

import logging
from collections.abc import Callable, Mapping
from threading import Lock
from typing import Any

from opentelemetry import metrics as otel_metrics
from opentelemetry.metrics import Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader

from app.core.config import Settings
from app.observability.tracing import INSTRUMENTATION_SCOPE, resource_attributes

logger = logging.getLogger(__name__)

# This is the enforced cardinality contract. Correlation IDs and user-controlled
# incident fields intentionally do not appear in any label set.
METRIC_LABEL_POLICY: dict[str, frozenset[str]] = {
    "service_health": frozenset(),
    "api_requests": frozenset({"method", "route", "status_code"}),
    "api_request_duration_seconds": frozenset({"method", "route", "status_code"}),
    "incidents_created": frozenset({"classification", "severity"}),
    "outbox_pending": frozenset(),
    "outbox_publish": frozenset({"event_type", "outcome"}),
    "outbox_publish_failures": frozenset({"event_type"}),
    "outbox_retry": frozenset({"event_type"}),
    "outbox_publish_duration_seconds": frozenset({"event_type", "outcome"}),
    "kafka_events_published": frozenset({"topic", "event_type"}),
    "kafka_publish_failures": frozenset({"topic", "event_type"}),
    "kafka_events_consumed": frozenset({"topic", "event_type"}),
    "kafka_events_processed": frozenset({"event_type", "outcome"}),
    "kafka_duplicates": frozenset({"event_type"}),
    "kafka_malformed": frozenset(),
    "kafka_processing_failures": frozenset({"event_type", "retryable"}),
    "kafka_processing_duration_seconds": frozenset({"event_type", "outcome"}),
    "kafka_offset_commit": frozenset({"topic", "outcome"}),
    "agent_executions": frozenset({"status", "classification"}),
    "agent_execution_failures": frozenset({"status", "classification"}),
    "agent_execution_retries": frozenset({"classification"}),
    "agent_planner_fallbacks": frozenset({"classification"}),
    "agent_execution_duration_seconds": frozenset({"status", "classification"}),
    "agent_steps": frozenset({"action_type", "outcome"}),
    "agent_tool_calls": frozenset({"tool_name", "status"}),
    "agent_tool_failures": frozenset({"tool_name"}),
    "agent_tool_duration_seconds": frozenset({"tool_name", "status"}),
    "agent_model_calls": frozenset(
        {"provider", "model", "operation", "prompt_name", "prompt_version"}
    ),
    "agent_model_failures": frozenset({"provider", "model", "operation"}),
    "agent_model_duration_seconds": frozenset({"provider", "model", "operation"}),
    "agent_retrieved_chunks": frozenset({"classification"}),
    "agent_review_approved": frozenset(),
    "agent_review_rejected": frozenset(),
    "rag_retrieval": frozenset({"classification", "outcome"}),
    "rag_retrieval_failures": frozenset({"classification"}),
    "rag_retrieval_duration_seconds": frozenset({"classification", "outcome"}),
    "rag_chunks_returned": frozenset({"classification"}),
    "rag_empty_retrieval": frozenset({"classification"}),
    "rag_invalid_citation": frozenset({"classification"}),
    "llm_requests": frozenset({"provider", "model", "operation", "status"}),
    "llm_failures": frozenset({"provider", "model", "operation"}),
    "llm_request_duration_seconds": frozenset(
        {"provider", "model", "operation", "status"}
    ),
    "llm_prompt_tokens": frozenset({"provider", "model", "operation"}),
    "llm_completion_tokens": frozenset({"provider", "model", "operation"}),
    "review_feedback": frozenset({"outcome"}),
    "review_approved": frozenset(),
    "review_rejected": frozenset(),
    "review_conflicts": frozenset({"outcome"}),
    "database_operations": frozenset({"db_system", "operation", "outcome"}),
    "database_failures": frozenset({"db_system", "operation"}),
    "database_operation_duration_seconds": frozenset(
        {"db_system", "operation", "outcome"}
    ),
}

_COUNTERS = frozenset(
    {
        "api_requests",
        "incidents_created",
        "outbox_publish",
        "outbox_publish_failures",
        "outbox_retry",
        "kafka_events_published",
        "kafka_publish_failures",
        "kafka_events_consumed",
        "kafka_events_processed",
        "kafka_duplicates",
        "kafka_malformed",
        "kafka_processing_failures",
        "kafka_offset_commit",
        "agent_executions",
        "agent_execution_failures",
        "agent_execution_retries",
        "agent_planner_fallbacks",
        "agent_steps",
        "agent_tool_calls",
        "agent_tool_failures",
        "agent_model_calls",
        "agent_model_failures",
        "agent_retrieved_chunks",
        "agent_review_approved",
        "agent_review_rejected",
        "rag_retrieval",
        "rag_retrieval_failures",
        "rag_empty_retrieval",
        "rag_invalid_citation",
        "llm_requests",
        "llm_failures",
        "llm_prompt_tokens",
        "llm_completion_tokens",
        "review_feedback",
        "review_approved",
        "review_rejected",
        "review_conflicts",
        "database_operations",
        "database_failures",
    }
)

_HISTOGRAMS = frozenset(
    {
        "api_request_duration_seconds",
        "outbox_publish_duration_seconds",
        "kafka_processing_duration_seconds",
        "agent_execution_duration_seconds",
        "agent_tool_duration_seconds",
        "agent_model_duration_seconds",
        "rag_retrieval_duration_seconds",
        "rag_chunks_returned",
        "llm_request_duration_seconds",
        "database_operation_duration_seconds",
    }
)


def filter_metric_attributes(
    metric_name: str, attributes: Mapping[str, Any] | None
) -> dict[str, str | int | float | bool]:
    """Drop undeclared and complex labels before they reach an exporter."""
    allowed = METRIC_LABEL_POLICY.get(metric_name, frozenset())
    result: dict[str, str | int | float | bool] = {}
    for key, value in (attributes or {}).items():
        if key not in allowed or not isinstance(value, (str, int, float, bool)):
            continue
        result[key] = value[:120] if isinstance(value, str) else value
    return result


class PlatformMetrics:
    """Small typed facade over a single process-wide OTel meter."""

    def __init__(self, provider: MeterProvider | None = None) -> None:
        meter = (
            provider.get_meter(INSTRUMENTATION_SCOPE)
            if provider is not None
            else otel_metrics.get_meter(INSTRUMENTATION_SCOPE)
        )
        self._meter = meter
        self._counters = {
            name: meter.create_counter(
                name,
                unit="{event}",
                description=f"AI support platform {name.replace('_', ' ')}.",
            )
            for name in _COUNTERS
        }
        self._histograms = {
            name: meter.create_histogram(
                name,
                unit="s" if name.endswith("_seconds") else "{chunk}",
                description=f"AI support platform {name.replace('_', ' ')}.",
            )
            for name in _HISTOGRAMS
        }
        self._observable_names: set[str] = set()
        self._meter.create_observable_gauge(
            "service_health",
            callbacks=[lambda _: [Observation(1)]],
            unit="1",
            description="One while this process can collect application telemetry.",
        )

    def count(
        self,
        name: str,
        value: int = 1,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        """Increment a declared counter; unknown instruments fail closed."""
        instrument = self._counters.get(name)
        if instrument is not None and value >= 0:
            instrument.add(value, filter_metric_attributes(name, attributes))

    def observe(
        self,
        name: str,
        value: float,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        """Record a non-negative histogram observation."""
        instrument = self._histograms.get(name)
        if instrument is not None and value >= 0:
            instrument.record(value, filter_metric_attributes(name, attributes))

    def observable_gauge(
        self,
        name: str,
        callback: Callable[[], int | float],
        *,
        description: str,
    ) -> None:
        """Register a single best-effort gauge callback for this process."""
        if name in self._observable_names:
            return

        def observe(_: Any) -> list[Observation]:
            try:
                return [Observation(callback())]
            except Exception:
                logger.exception(
                    "telemetry_gauge_callback_failed", extra={"metric": name}
                )
                return []

        self._meter.create_observable_gauge(
            name,
            callbacks=[observe],
            unit="{event}",
            description=description,
        )
        self._observable_names.add(name)


_lock = Lock()
_provider: MeterProvider | None = None
_platform_metrics = PlatformMetrics()


def configure_metrics(
    settings: Settings,
    *,
    metric_reader: MetricReader | None = None,
    force: bool = False,
) -> MeterProvider | None:
    """Initialize one OTLP metric pipeline, or remain a no-op when disabled."""
    global _provider, _platform_metrics
    enabled = settings.otel_metrics_exporter == "otlp" or metric_reader is not None
    if not enabled:
        if force:
            shutdown_metrics()
        return None
    with _lock:
        if _provider is not None and not force:
            return _provider
        if _provider is not None:
            _provider.shutdown()
        try:
            readers: list[MetricReader]
            if metric_reader is not None:
                readers = [metric_reader]
            else:
                from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                    OTLPMetricExporter,
                )
                from opentelemetry.sdk.metrics.export import (
                    PeriodicExportingMetricReader,
                )

                exporter = OTLPMetricExporter(
                    endpoint=settings.otel_exporter_otlp_endpoint,
                    insecure=settings.otel_exporter_otlp_endpoint.startswith("http://"),
                    timeout=3,
                )
                readers = [
                    PeriodicExportingMetricReader(
                        exporter,
                        export_interval_millis=5_000,
                        export_timeout_millis=3_000,
                    )
                ]
            provider = MeterProvider(
                resource=otel_resource(settings),
                metric_readers=readers,
            )
            _provider = provider
            _platform_metrics = PlatformMetrics(provider)
        except Exception:
            _provider = None
            _platform_metrics = PlatformMetrics()
            logger.exception("telemetry_metrics_initialization_failed")
    return _provider


def otel_resource(settings: Settings):  # type: ignore[no-untyped-def]
    """Delay the SDK Resource import during telemetry-disabled unit tests."""
    from opentelemetry.sdk.resources import Resource

    return Resource.create(resource_attributes(settings))


def get_metrics() -> PlatformMetrics:
    return _platform_metrics


def get_meter_provider() -> MeterProvider | None:
    return _provider


def shutdown_metrics() -> None:
    """Flush metric export without propagating exporter failures."""
    global _provider, _platform_metrics
    with _lock:
        provider, _provider = _provider, None
        _platform_metrics = PlatformMetrics()
    if provider is not None:
        try:
            provider.shutdown()
        except Exception:
            logger.exception("telemetry_metrics_shutdown_failed")
