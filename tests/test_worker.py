"""Worker processing, malformed-message, and idempotency tests."""

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.models import IncidentRecord, ProcessedEventRecord
from app.events.models import IncidentCreatedEvent
from app.models.incident import IncidentClassification, Severity
from app.workers.processor import (
    IncidentEventProcessor,
    ProcessingOutcome,
    RetryableProcessingError,
)
from app.workers.runner import (
    IncidentMessageHandler,
    KafkaIncidentWorker,
    MessageOutcome,
)


def persist_incident(session_factory: sessionmaker[Session]) -> IncidentRecord:
    """Persist the incident that a worker event references."""
    with session_factory() as session:
        incident = IncidentRecord(
            service="identity-api",
            error="JWT expired",
            log="Bearer token has expired",
            requested_severity=None,
            resolved_severity="high",
            classification="authentication_error",
            probable_cause="Expired token",
            recommended_actions=["Refresh the token"],
        )
        session.add(incident)
        session.commit()
        return incident


def event_for(incident_id: int) -> IncidentCreatedEvent:
    """Build a valid event for a chosen incident ID."""
    return IncidentCreatedEvent.create(
        incident_id=incident_id,
        service="identity-api",
        classification=IncidentClassification.AUTHENTICATION_ERROR,
        severity=Severity.HIGH,
    )


def test_worker_processing_is_idempotent(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_incident(session_factory)
    processor = IncidentEventProcessor(session_factory, "incident-processing-v1")
    event = event_for(incident.id)

    first = processor.process(event)
    duplicate = processor.process(event)

    assert first is ProcessingOutcome.PROCESSED
    assert duplicate is ProcessingOutcome.DUPLICATE
    with session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(ProcessedEventRecord)) == 1
        )
        record = session.get(ProcessedEventRecord, str(event.event_id))
        assert record is not None
        assert record.processing_result == {
            "phase": 3,
            "status": "prepared_for_future_ai_processing",
            "routing_key": "authentication_error:high",
        }


def test_missing_incident_is_retryable(
    session_factory: sessionmaker[Session],
) -> None:
    processor = IncidentEventProcessor(session_factory, "incident-processing-v1")

    with pytest.raises(RetryableProcessingError):
        processor.process(event_for(9999))


def test_message_handler_safely_rejects_malformed_event(
    session_factory: sessionmaker[Session],
) -> None:
    handler = IncidentMessageHandler(
        IncidentEventProcessor(session_factory, "incident-processing-v1")
    )

    assert handler.handle(b"not-json") is MessageOutcome.MALFORMED
    assert handler.handle(None) is MessageOutcome.MALFORMED


def test_message_handler_requests_retry_for_missing_incident(
    session_factory: sessionmaker[Session],
) -> None:
    handler = IncidentMessageHandler(
        IncidentEventProcessor(session_factory, "incident-processing-v1")
    )

    assert handler.handle(event_for(9999).serialize()) is MessageOutcome.RETRY


def test_worker_consumer_configuration_requires_manual_offsets(
    session_factory: sessionmaker[Session],
) -> None:
    captured: dict[str, object] = {}

    class FakeConsumer:
        pass

    class FakeTopicManager:
        pass

    def factory(config: dict[str, object]) -> Any:
        captured.update(config)
        return FakeConsumer()

    KafkaIncidentWorker(
        get_settings(),
        IncidentMessageHandler(
            IncidentEventProcessor(session_factory, "incident-processing-v1")
        ),
        consumer_factory=factory,
        topic_manager=FakeTopicManager(),
    )

    assert captured["group.id"] == "incident-processing-v1"
    assert captured["enable.auto.commit"] is False
    assert captured["enable.auto.offset.store"] is False
    assert captured["auto.offset.reset"] == "earliest"
    assert captured["max.poll.interval.ms"] == 900_000
    assert captured["session.timeout.ms"] == 45_000
    assert captured["heartbeat.interval.ms"] == 3_000


def test_worker_closes_consumer_when_stopped_before_readiness(
    session_factory: sessionmaker[Session],
) -> None:
    class FakeConsumer:
        closed = False

        def close(self) -> None:
            self.closed = True

    class FakeTopicManager:
        pass

    consumer = FakeConsumer()
    worker = KafkaIncidentWorker(
        get_settings(),
        IncidentMessageHandler(
            IncidentEventProcessor(session_factory, "incident-processing-v1")
        ),
        consumer=consumer,
        topic_manager=FakeTopicManager(),
    )
    stop = __import__("threading").Event()
    stop.set()

    worker.run(stop)

    assert consumer.closed is True
