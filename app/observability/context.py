"""Safe trace-context propagation and structured-log correlation."""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_PROPAGATOR = TraceContextTextMapPropagator()
_ALLOWED_TRACE_HEADERS = frozenset({"traceparent", "tracestate"})


def active_trace_ids() -> dict[str, str]:
    """Return lowercase W3C identifiers only while a recording span is active."""
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return {}
    return {
        "trace_id": format(span_context.trace_id, "032x"),
        "span_id": format(span_context.span_id, "016x"),
    }


def capture_trace_context() -> dict[str, str]:
    """Capture only W3C propagation headers for durable outbox retries."""
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return {
        key: value
        for key, value in carrier.items()
        if key.lower() in _ALLOWED_TRACE_HEADERS
    }


def extract_trace_context(carrier: Mapping[str, str] | None) -> Context:
    """Extract a W3C parent context from a trusted, bounded carrier."""
    safe_carrier = {
        key.lower(): value[:512]
        for key, value in (carrier or {}).items()
        if key.lower() in _ALLOWED_TRACE_HEADERS and isinstance(value, str)
    }
    return _PROPAGATOR.extract(carrier=safe_carrier)


@contextmanager
def use_trace_context(carrier: Mapping[str, str] | None) -> Iterator[None]:
    """Temporarily attach an extracted parent for deferred background work."""
    token = otel_context.attach(extract_trace_context(carrier))
    try:
        yield
    finally:
        otel_context.detach(token)


def inject_kafka_headers(
    existing: Sequence[tuple[str, bytes | str | None]] | None = None,
) -> list[tuple[str, bytes | str | None]]:
    """Append W3C context to Kafka headers without altering the event envelope."""
    headers = [
        (key, value)
        for key, value in (existing or [])
        if key.lower() not in _ALLOWED_TRACE_HEADERS
    ]
    headers.extend(
        (key, value.encode("ascii")) for key, value in capture_trace_context().items()
    )
    return headers


def extract_kafka_context(
    headers: Sequence[tuple[str, bytes | str | None]] | None,
) -> Context:
    """Extract W3C context from confluent-kafka's header representation."""
    carrier: dict[str, str] = {}
    for key, value in headers or []:
        if key.lower() not in _ALLOWED_TRACE_HEADERS or value is None:
            continue
        if isinstance(value, bytes):
            try:
                decoded = value.decode("ascii")
            except UnicodeDecodeError:
                continue
        else:
            decoded = value
        carrier[key.lower()] = decoded[:512]
    return extract_trace_context(carrier)


def set_current_span_attributes(attributes: Mapping[str, Any]) -> None:
    """Add safe scalar correlation fields to the active span."""
    span = trace.get_current_span()
    if not span.is_recording():
        return
    for key, value in attributes.items():
        if value is None or not isinstance(value, (bool, int, float, str)):
            continue
        span.set_attribute(key, value[:256] if isinstance(value, str) else value)
