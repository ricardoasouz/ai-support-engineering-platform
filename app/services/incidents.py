"""Incident application workflow, independent of HTTP routing."""

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.incident import IncidentAnalysisResponse, IncidentRequest
from app.repositories.incidents import IncidentRepository
from app.services.analyzer import analyze_incident

logger = logging.getLogger(__name__)


def analyze_and_persist_incident(
    incident: IncidentRequest,
    session: Session,
) -> IncidentAnalysisResponse:
    """Analyze an incident and atomically persist its input and result."""
    analysis = analyze_incident(incident)
    repository = IncidentRepository(session)
    try:
        record = repository.create(incident, analysis)
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception(
            "incident_persistence_failed",
            extra={"service": incident.service},
        )
        raise

    logger.info(
        "incident_analyzed",
        extra={
            "incident_id": record.id,
            "service": record.service,
            "classification": record.classification,
            "resolved_severity": record.resolved_severity,
        },
    )
    return analysis
