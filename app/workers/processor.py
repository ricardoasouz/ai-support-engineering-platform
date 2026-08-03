"""Idempotent incident-event processing with a durable Phase 4 workflow."""

import logging
from enum import Enum
from typing import Protocol

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.ai.workflow import ResolutionWorkflowOutcome, RetryableResolutionError
from app.db.models import IncidentRecord, ProcessedEventRecord
from app.events.models import IncidentCreatedEvent

logger = logging.getLogger(__name__)


class ProcessingOutcome(str, Enum):
    """Result of deterministic processing for one event."""

    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    FAILED = "failed"


class RetryableProcessingError(RuntimeError):
    """A transient processing problem for which Kafka should redeliver."""


class ResolutionWorkflow(Protocol):
    """Worker-facing contract shared by Phase 4 and controlled Phase 5 flows."""

    phase: int

    def resolve(self, event: IncidentCreatedEvent) -> ResolutionWorkflowOutcome: ...


class IncidentEventProcessor:
    """Validate incident existence and store exactly one processing record."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        consumer_group: str,
        workflow: ResolutionWorkflow | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.consumer_group = consumer_group
        self.workflow = workflow

    def process(self, event: IncidentCreatedEvent) -> ProcessingOutcome:
        """Run the configured workflow before writing its idempotency marker."""
        event_id = str(event.event_id)
        with self.session_factory() as session:
            if session.get(ProcessedEventRecord, event_id) is not None:
                logger.info(
                    "incident_event_duplicate",
                    extra={
                        "event_id": event_id,
                        "incident_id": event.incident_id,
                    },
                )
                return ProcessingOutcome.DUPLICATE

            incident = session.get(IncidentRecord, event.incident_id)
            if incident is None:
                raise RetryableProcessingError(
                    f"Incident {event.incident_id} is not visible in PostgreSQL"
                )

        if self.workflow is None:
            # Retain the deterministic Phase 3 processor for broker-free legacy tests.
            processing_result: dict[str, object] = {
                "phase": 3,
                "status": "prepared_for_future_ai_processing",
                "routing_key": f"{event.classification.value}:{event.severity.value}",
            }
            outcome = ProcessingOutcome.PROCESSED
        else:
            try:
                resolution_outcome = self.workflow.resolve(event)
            except RetryableResolutionError as exc:
                raise RetryableProcessingError(str(exc)) from exc
            processing_result = {
                "phase": self.workflow.phase,
                "status": resolution_outcome.value,
                "routing_key": f"{event.classification.value}:{event.severity.value}",
            }
            outcome = (
                ProcessingOutcome.PROCESSED
                if resolution_outcome is ResolutionWorkflowOutcome.COMPLETED
                else ProcessingOutcome.FAILED
            )

        with self.session_factory() as session:
            if session.get(ProcessedEventRecord, event_id) is not None:
                return ProcessingOutcome.DUPLICATE
            session.add(
                ProcessedEventRecord(
                    event_id=event_id,
                    event_type=event.event_type,
                    event_version=event.event_version,
                    incident_id=event.incident_id,
                    consumer_group=self.consumer_group,
                    processing_result=processing_result,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                logger.info(
                    "incident_event_duplicate",
                    extra={
                        "event_id": event_id,
                        "incident_id": event.incident_id,
                    },
                )
                return ProcessingOutcome.DUPLICATE

        logger.info(
            "incident_event_processed",
            extra={
                "event_id": event_id,
                "incident_id": event.incident_id,
                "classification": event.classification.value,
                "severity": event.severity.value,
                "routing_key": processing_result["routing_key"],
                "outcome": outcome.value,
            },
        )
        return outcome
