"""Transactional outbox durability and failure-handling tests."""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import IncidentRecord, OutboxEventRecord
from app.events.dispatcher import DispatchOutcome, OutboxDispatcher
from app.events.producer import EventPublishError
from app.models.incident import IncidentRequest
from app.services.incidents import analyze_and_persist_incident


class UnavailableProducer:
    """Always report a broker outage."""

    def publish(self, _event: object, _topic: str) -> None:
        raise EventPublishError("broker unavailable")

    def close(self) -> None:
        pass


def test_kafka_failure_keeps_incident_and_pending_outbox_event(
    session_factory: sessionmaker[Session],
) -> None:
    dispatcher = OutboxDispatcher(
        session_factory,
        UnavailableProducer(),
        batch_size=10,
    )
    incident = IncidentRequest(
        service="payments-api",
        error="Gateway timeout",
        log="Upstream request timed out",
    )

    with session_factory() as session:
        result = analyze_and_persist_incident(incident, session, dispatcher)

    with session_factory() as session:
        stored_incident = session.scalar(select(IncidentRecord))
        outbox = session.scalar(select(OutboxEventRecord))

    assert result.classification.value == "timeout_error"
    assert stored_incident is not None
    assert outbox is not None
    assert outbox.incident_id == stored_incident.id
    assert outbox.published_at is None
    assert outbox.attempts == 1
    assert outbox.last_error == "broker unavailable"


def test_unexpected_dispatcher_failure_does_not_rollback_incident(
    session_factory: sessionmaker[Session],
) -> None:
    class BrokenDispatcher:
        def dispatch_event(self, _event_id: str) -> DispatchOutcome:
            raise RuntimeError("unexpected dispatcher failure")

    with session_factory() as session:
        analyze_and_persist_incident(
            IncidentRequest(
                service="catalog-api",
                error="Unexpected error",
                log="No known pattern",
            ),
            session,
            BrokenDispatcher(),
        )

    with session_factory() as session:
        assert session.scalar(select(IncidentRecord)) is not None
        assert session.scalar(select(OutboxEventRecord)) is not None
