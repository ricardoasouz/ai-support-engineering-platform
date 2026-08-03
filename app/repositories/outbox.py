"""Persistence operations for the transactional event outbox."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import OutboxEventRecord
from app.events.models import IncidentCreatedEvent
from app.observability.context import capture_trace_context


class OutboxRepository:
    """Encapsulate durable event staging and dispatch state changes."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, event: IncidentCreatedEvent, topic: str) -> OutboxEventRecord:
        """Stage an event in the incident database transaction."""
        record = OutboxEventRecord(
            event_id=str(event.event_id),
            incident_id=event.incident_id,
            event_type=event.event_type,
            topic=topic,
            payload=event.model_dump(mode="json"),
            trace_context=capture_trace_context() or None,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def get_for_dispatch(self, event_id: str) -> OutboxEventRecord | None:
        """Lock an unpublished event, skipping rows held by another dispatcher."""
        statement = (
            select(OutboxEventRecord)
            .where(
                OutboxEventRecord.event_id == event_id,
                OutboxEventRecord.published_at.is_(None),
            )
            .with_for_update(skip_locked=True)
        )
        return self.session.scalar(statement)

    def list_pending_ids(self, *, now: datetime, limit: int) -> list[str]:
        """Return event IDs that are due for another publication attempt."""
        statement = (
            select(OutboxEventRecord.event_id)
            .where(
                OutboxEventRecord.published_at.is_(None),
                OutboxEventRecord.next_attempt_at <= now,
            )
            .order_by(OutboxEventRecord.created_at, OutboxEventRecord.event_id)
            .limit(limit)
        )
        return list(self.session.scalars(statement))
