"""Deterministic Phase 6 telemetry, propagation, privacy, and isolation tests."""

import json
import logging
import time
from collections.abc import Iterator
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.models import FeedbackRequest
from app.agent.workflow import ControlledAgentWorkflow
from app.ai.providers.ollama import OllamaEmbeddingProvider
from app.api.routes.incidents import _record_feedback
from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.db.models import (
    AgentExecutionRecord,
    AIResolutionRecord,
    OutboxEventRecord,
)
from app.events.dispatcher import OutboxDispatcher
from app.events.producer import EventPublishError, KafkaEventProducer
from app.models.incident import IncidentRequest
from app.observability.context import (
    active_trace_ids,
    capture_trace_context,
    extract_kafka_context,
    inject_kafka_headers,
)
from app.observability.instrumentation import (
    configure_observability,
    shutdown_observability,
)
from app.observability.metrics import METRIC_LABEL_POLICY, filter_metric_attributes
from app.observability.metrics import get_metrics as platform_metrics
from app.observability.tracing import start_span
from app.services.incidents import analyze_and_persist_incident
from tests.test_worker import persist_incident


def telemetry_settings(*, enabled: bool = True) -> Settings:
    exporter = "otlp" if enabled else "none"
    return Settings(
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        KAFKA_ENABLED=False,
        OTEL_SERVICE_NAME="phase6-test",
        OTEL_TRACES_EXPORTER=exporter,
        OTEL_METRICS_EXPORTER=exporter,
        OTEL_EXPORTER_OTLP_ENDPOINT="http://127.0.0.1:4317",
    )


@pytest.fixture
def telemetry() -> Iterator[tuple[InMemorySpanExporter, InMemoryMetricReader]]:
    spans = InMemorySpanExporter()
    metrics = InMemoryMetricReader()
    configure_observability(
        telemetry_settings(),
        span_exporter=spans,
        metric_reader=metrics,
        force=True,
    )
    try:
        yield spans, metrics
    finally:
        shutdown_observability()


def metric_points(reader: InMemoryMetricReader, name: str):  # type: ignore[no-untyped-def]
    data = reader.get_metrics_data()
    if data is None:
        return []
    return [
        point
        for resource_metrics in data.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


def test_w3c_context_crosses_kafka_headers_and_preserves_trace(telemetry) -> None:  # type: ignore[no-untyped-def]
    spans, _ = telemetry
    with start_span("api request"):
        with start_span("kafka producer") as producer_span:
            headers = inject_kafka_headers()
            carrier = capture_trace_context()
        with start_span(
            "kafka consumer", context=extract_kafka_context(headers)
        ) as consumer_span:
            assert active_trace_ids()["trace_id"] == format(
                producer_span.get_span_context().trace_id, "032x"
            )

    assert set(carrier) <= {"traceparent", "tracestate"}
    assert any(key == "traceparent" for key, _ in headers)
    assert (
        consumer_span.get_span_context().trace_id
        == producer_span.get_span_context().trace_id
    )
    assert consumer_span.parent.span_id == producer_span.get_span_context().span_id
    assert {span.name for span in spans.get_finished_spans()} >= {
        "api request",
        "kafka producer",
        "kafka consumer",
    }


def test_json_logging_is_enriched_only_with_active_trace_ids(telemetry) -> None:  # type: ignore[no-untyped-def]
    formatter = JsonFormatter()
    outside = json.loads(formatter.format(logging.makeLogRecord({"msg": "outside"})))
    with start_span("log correlation"):
        expected = active_trace_ids()
        inside = json.loads(formatter.format(logging.makeLogRecord({"msg": "inside"})))

    assert "trace_id" not in outside and "span_id" not in outside
    assert inside["trace_id"] == expected["trace_id"]
    assert inside["span_id"] == expected["span_id"]


def test_metrics_increment_and_forbid_high_cardinality_labels(telemetry) -> None:  # type: ignore[no-untyped-def]
    _, reader = telemetry
    platform_metrics().count(
        "kafka_events_processed",
        attributes={
            "event_type": "incident.created",
            "outcome": "processed",
            "incident_id": "must-drop",
            "raw_log": "must-drop",
        },
    )

    points = metric_points(reader, "kafka_events_processed")
    assert sum(point.value for point in points) == 1
    assert dict(points[0].attributes) == {
        "event_type": "incident.created",
        "outcome": "processed",
    }
    forbidden = {
        "incident_id",
        "event_id",
        "execution_id",
        "error",
        "log",
        "raw_log",
    }
    assert not any(forbidden & labels for labels in METRIC_LABEL_POLICY.values())
    assert filter_metric_attributes(
        "api_requests", {"method": "POST", "incident_id": 42}
    ) == {"method": "POST"}


class UnavailableProducer:
    def publish(self, _event: object, _topic: str) -> None:
        raise EventPublishError("broker unavailable")

    def close(self) -> None:
        pass


def test_outbox_trace_context_and_failure_metrics(
    telemetry,
    session_factory: sessionmaker[Session],
) -> None:
    _, reader = telemetry
    dispatcher = OutboxDispatcher(session_factory, UnavailableProducer(), batch_size=10)
    with (
        start_span("POST /api/v1/incidents") as request_span,
        session_factory() as session,
    ):
        analyze_and_persist_incident(
            IncidentRequest(
                service="payments-api",
                error="Gateway timeout",
                log="Upstream timed out",
            ),
            session,
            dispatcher,
        )
    with session_factory() as session:
        record = session.scalar(select(OutboxEventRecord))

    assert record is not None and record.trace_context is not None
    assert record.trace_context["traceparent"].split("-")[1] == format(
        request_span.get_span_context().trace_id, "032x"
    )
    assert sum(point.value for point in metric_points(reader, "outbox_retry")) == 1
    assert (
        sum(point.value for point in metric_points(reader, "outbox_publish_failures"))
        == 1
    )


def test_provider_and_agent_metrics_use_controlled_labels(telemetry) -> None:  # type: ignore[no-untyped-def]
    _, reader = telemetry
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})
        ),
        base_url="http://ollama",
    )
    provider = OllamaEmbeddingProvider("http://unused", "embed-test", 2, 1, client)
    assert provider.embed_texts(["bounded query"]) == [[0.1, 0.2]]
    ControlledAgentWorkflow._record_execution_metrics(
        status="awaiting_review",
        classification="timeout_error",
        started=time.perf_counter(),
    )

    llm_points = metric_points(reader, "llm_requests")
    agent_points = metric_points(reader, "agent_executions")
    assert dict(llm_points[0].attributes) == {
        "provider": "ollama",
        "model": "embed-test",
        "operation": "embedding",
        "status": "success",
    }
    assert dict(agent_points[0].attributes) == {
        "status": "awaiting_review",
        "classification": "timeout_error",
    }


def test_review_metrics_and_conflict_are_recorded(
    telemetry,
    session_factory: sessionmaker[Session],
) -> None:
    _, reader = telemetry
    incident = persist_incident(session_factory)
    event_id = str(uuid4())
    with session_factory() as session:
        session.add(
            AIResolutionRecord(
                incident_id=incident.id,
                event_id=event_id,
                status="completed",
                attempt_count=1,
            )
        )
        session.add(
            AgentExecutionRecord(
                execution_id=str(uuid4()),
                incident_id=incident.id,
                event_id=event_id,
                status="awaiting_review",
                provider="fake",
                model="test-model",
                planner_prompt_name="planner",
                planner_prompt_version="v1",
                resolver_prompt_name="resolver",
                resolver_prompt_version="v1",
            )
        )
        session.commit()

    feedback = FeedbackRequest(reviewer="demo-reviewer")
    with session_factory() as session:
        result = _record_feedback(incident.id, feedback, session, outcome="approved")
    with session_factory() as session, pytest.raises(HTTPException) as conflict:
        _record_feedback(incident.id, feedback, session, outcome="approved")

    assert result.outcome == "approved"
    assert conflict.value.status_code == 409
    assert sum(point.value for point in metric_points(reader, "review_approved")) == 1
    assert sum(point.value for point in metric_points(reader, "review_conflicts")) == 1


class RaisingExporter(SpanExporter):
    def export(self, _spans):  # type: ignore[no-untyped-def]
        raise RuntimeError("collector unavailable")

    def shutdown(self) -> None:
        pass


def test_exporter_failure_and_disabled_telemetry_never_break_business_code() -> None:
    configure_observability(
        telemetry_settings(), span_exporter=RaisingExporter(), force=True
    )
    try:
        with start_span("business operation"):
            business_result = 6 * 7
        assert business_result == 42
    finally:
        shutdown_observability()

    configure_observability(telemetry_settings(enabled=False), force=True)
    try:
        with start_span("disabled telemetry"):
            platform_metrics().count("incidents_created")
            assert 2 + 2 == 4
    finally:
        shutdown_observability()


def test_kafka_producer_injects_current_context(telemetry) -> None:  # type: ignore[no-untyped-def]
    class TopicManager:
        def ensure_topic(self, _topic: str) -> None:
            pass

    class Producer:
        def __init__(self) -> None:
            self.message = None

        def produce(self, **message):  # type: ignore[no-untyped-def]
            self.message = message
            message["on_delivery"](None, object())

        def flush(self, _timeout: float) -> int:
            return 0

    from tests.test_events import build_event

    fake = Producer()
    producer = KafkaEventProducer(
        telemetry_settings(), producer=fake, topic_manager=TopicManager()
    )
    with start_span("outbox dispatch"):
        producer.publish(build_event(), "incident.created")

    assert fake.message is not None
    assert any(key == "traceparent" for key, _ in fake.message["headers"])
