"""OpenTelemetry tracer lifecycle with best-effort OTLP export."""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from threading import Lock
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer

from app.core.config import Settings

logger = logging.getLogger(__name__)
INSTRUMENTATION_SCOPE = "ai-support-engineering-platform"
_lock = Lock()
_provider: TracerProvider | None = None
_service_name: str | None = None


class SafeSpanExporter(SpanExporter):
    """Contain exporter defects so span completion cannot affect business code."""

    def __init__(self, exporter: SpanExporter) -> None:
        self._exporter = exporter

    def export(self, spans):  # type: ignore[no-untyped-def]
        try:
            return self._exporter.export(spans)
        except Exception:
            logger.exception("telemetry_span_export_failed")
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self._exporter.shutdown()
        except Exception:
            logger.exception("telemetry_span_exporter_shutdown_failed")

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        try:
            return self._exporter.force_flush(timeout_millis)
        except Exception:
            logger.exception("telemetry_span_exporter_flush_failed")
            return False


def resource_attributes(settings: Settings) -> dict[str, str]:
    """Build bounded resource attributes from standard comma-separated settings."""
    attributes: dict[str, str] = {"service.name": settings.otel_service_name}
    for item in settings.otel_resource_attributes.split(","):
        key, separator, value = item.strip().partition("=")
        if separator and key and value:
            attributes[key[:128]] = value[:256]
    return attributes


def configure_tracing(
    settings: Settings,
    *,
    span_exporter: SpanExporter | None = None,
    force: bool = False,
) -> TracerProvider | None:
    """Initialize a process tracer provider once; exporter errors stay isolated."""
    global _provider, _service_name
    enabled = settings.otel_traces_exporter == "otlp" or span_exporter is not None
    if not enabled:
        if force:
            shutdown_tracing()
        return None
    with _lock:
        if _provider is not None and not force:
            return _provider
        if _provider is not None:
            _provider.shutdown()
        try:
            provider = TracerProvider(
                resource=Resource.create(resource_attributes(settings))
            )
            if span_exporter is not None:
                provider.add_span_processor(
                    SimpleSpanProcessor(SafeSpanExporter(span_exporter))
                )
            else:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )

                exporter = OTLPSpanExporter(
                    endpoint=settings.otel_exporter_otlp_endpoint,
                    insecure=settings.otel_exporter_otlp_endpoint.startswith("http://"),
                    timeout=3,
                )
                provider.add_span_processor(
                    BatchSpanProcessor(SafeSpanExporter(exporter))
                )
            _provider = provider
            _service_name = settings.otel_service_name
        except Exception:
            _provider = None
            _service_name = None
            logger.exception("telemetry_tracing_initialization_failed")
    return _provider


def get_tracer(name: str = INSTRUMENTATION_SCOPE) -> Tracer:
    """Return the configured tracer or OpenTelemetry's no-op tracer."""
    provider = _provider
    return provider.get_tracer(name) if provider is not None else trace.get_tracer(name)


@contextmanager
def start_span(
    name: str,
    *,
    attributes: Mapping[str, Any] | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
    context: Any | None = None,
) -> Iterator[Span]:
    """Create a current span while retaining business exceptions unchanged."""
    safe_attributes = {
        key: value[:256] if isinstance(value, str) else value
        for key, value in (attributes or {}).items()
        if isinstance(value, (bool, int, float, str))
    }
    with get_tracer().start_as_current_span(
        name,
        context=context,
        kind=kind,
        attributes=safe_attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        yield span


def mark_span_error(span: Span, error: BaseException) -> None:
    """Mark failure without exporting exception messages, stacks, or payloads."""
    if span.is_recording():
        span.set_attribute("error.type", type(error).__name__[:120])
        span.set_status(Status(StatusCode.ERROR, type(error).__name__[:120]))


def get_tracer_provider() -> TracerProvider | None:
    return _provider


def shutdown_tracing() -> None:
    """Flush and stop the current provider without affecting core shutdown."""
    global _provider, _service_name
    with _lock:
        provider, _provider = _provider, None
        _service_name = None
    if provider is not None:
        try:
            provider.shutdown()
        except Exception:
            logger.exception("telemetry_tracing_shutdown_failed")
