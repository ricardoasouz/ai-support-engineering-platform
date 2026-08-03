"""Incident application workflow, independent of HTTP routing."""

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.events.dispatcher import OutboxDispatcher
from app.events.models import IncidentCreatedEvent
from app.models.incident import IncidentAnalysisResponse, IncidentRequest
from app.observability.context import set_current_span_attributes
from app.observability.metrics import get_metrics
from app.observability.tracing import mark_span_error, start_span
from app.repositories.incidents import IncidentRepository
from app.repositories.outbox import OutboxRepository
from app.services.analyzer import analyze_incident

logger = logging.getLogger(__name__)


def analyze_and_persist_incident(
    incident: IncidentRequest,
    session: Session,
    dispatcher: OutboxDispatcher,
) -> IncidentAnalysisResponse:
    """Persist an incident and outbox event, then attempt post-commit publication."""
    with start_span("incident deterministic analysis"):
        analysis = analyze_incident(incident)
    repository = IncidentRepository(session)
    with start_span("incident and outbox transaction") as transaction_span:
        try:
            record = repository.create(incident, analysis)
            event = IncidentCreatedEvent.create(
                incident_id=record.id,
                service=record.service,
                classification=analysis.classification,
                severity=analysis.severity,
            )
            OutboxRepository(session).add(
                event,
                get_settings().kafka_incident_created_topic,
            )
            session.commit()
        except SQLAlchemyError as exc:
            mark_span_error(transaction_span, exc)
            session.rollback()
            logger.exception(
                "incident_persistence_failed",
                extra={"service": incident.service},
            )
            raise

    set_current_span_attributes(
        {
            "incident.id": record.id,
            "incident.service": record.service,
            "incident.classification": record.classification,
            "incident.severity": record.resolved_severity,
            "event.id": str(event.event_id),
        }
    )
    get_metrics().count(
        "incidents_created",
        attributes={
            "classification": record.classification,
            "severity": record.resolved_severity,
        },
    )

    logger.info(
        "incident_analyzed",
        extra={
            "incident_id": record.id,
            "service": record.service,
            "classification": record.classification,
            "resolved_severity": record.resolved_severity,
            "event_id": str(event.event_id),
        },
    )

    try:
        outcome = dispatcher.dispatch_event(str(event.event_id))
        logger.info(
            "incident_event_dispatch_attempted",
            extra={
                "incident_id": record.id,
                "event_id": str(event.event_id),
                "outcome": outcome.value,
            },
        )
    except Exception:
        logger.exception(
            "incident_event_dispatch_failed",
            extra={
                "incident_id": record.id,
                "event_id": str(event.event_id),
            },
        )
    return analysis
