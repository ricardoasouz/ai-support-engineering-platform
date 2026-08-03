"""Transactional outbox dispatch and retry loop."""

import logging
from datetime import UTC, datetime, timedelta
from enum import Enum
from threading import Event, Thread

from sqlalchemy.orm import Session, sessionmaker

from app.events.models import IncidentCreatedEvent
from app.events.producer import EventProducer, EventPublishError
from app.repositories.outbox import OutboxRepository

logger = logging.getLogger(__name__)


class DispatchOutcome(str, Enum):
    """Observable result of one outbox dispatch attempt."""

    PUBLISHED = "published"
    DEFERRED = "deferred"
    UNAVAILABLE = "unavailable"


class OutboxDispatcher:
    """Publish committed outbox rows and record retry state."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        producer: EventProducer,
        *,
        batch_size: int,
    ) -> None:
        self.session_factory = session_factory
        self.producer = producer
        self.batch_size = batch_size

    def dispatch_event(self, event_id: str) -> DispatchOutcome:
        """Publish one pending event without modifying its incident transaction."""
        with self.session_factory() as session:
            repository = OutboxRepository(session)
            record = repository.get_for_dispatch(event_id)
            if record is None:
                return DispatchOutcome.UNAVAILABLE

            try:
                event = IncidentCreatedEvent.model_validate(record.payload)
                self.producer.publish(event, record.topic)
            except (EventPublishError, ValueError) as exc:
                record.attempts += 1
                record.last_error = str(exc)[:2_000]
                record.next_attempt_at = datetime.now(UTC) + self._retry_delay(
                    record.attempts
                )
                session.commit()
                logger.warning(
                    "event_publish_deferred",
                    extra={
                        "event_id": event_id,
                        "event_type": record.event_type,
                        "incident_id": record.incident_id,
                        "attempt": record.attempts,
                        "error": str(exc),
                    },
                )
                return DispatchOutcome.DEFERRED

            record.attempts += 1
            record.published_at = datetime.now(UTC)
            record.last_error = None
            session.commit()
            logger.info(
                "event_published",
                extra={
                    "event_id": event_id,
                    "event_type": record.event_type,
                    "incident_id": record.incident_id,
                    "topic": record.topic,
                    "attempt": record.attempts,
                },
            )
            return DispatchOutcome.PUBLISHED

    def dispatch_pending(self) -> int:
        """Dispatch a bounded batch, stopping early while Kafka is unavailable."""
        with self.session_factory() as session:
            event_ids = OutboxRepository(session).list_pending_ids(
                now=datetime.now(UTC),
                limit=self.batch_size,
            )

        published = 0
        for event_id in event_ids:
            outcome = self.dispatch_event(event_id)
            if outcome is DispatchOutcome.PUBLISHED:
                published += 1
            elif outcome is DispatchOutcome.DEFERRED:
                break
        return published

    def close(self) -> None:
        """Release the underlying producer."""
        self.producer.close()

    @staticmethod
    def _retry_delay(attempt: int) -> timedelta:
        return timedelta(seconds=min(2 ** min(attempt, 6), 60))


class OutboxRetryService:
    """Background thread that retries committed, unpublished outbox events."""

    def __init__(self, dispatcher: OutboxDispatcher, poll_interval: float) -> None:
        self.dispatcher = dispatcher
        self.poll_interval = poll_interval
        self._stop = Event()
        self._thread = Thread(
            target=self._run,
            name="outbox-dispatcher",
            daemon=True,
        )

    def start(self) -> None:
        """Start the retry loop once."""
        self._thread.start()
        logger.info("outbox_retry_started")

    def stop(self) -> None:
        """Stop the retry loop and flush the producer."""
        self._stop.set()
        self._thread.join(timeout=self.poll_interval + 5)
        self.dispatcher.close()
        logger.info("outbox_retry_stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.dispatcher.dispatch_pending()
            except Exception:
                logger.exception("outbox_retry_failed")
            self._stop.wait(self.poll_interval)
