"""Database access operations for incidents."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import IncidentRecord
from app.models.incident import (
    IncidentAnalysisResponse,
    IncidentClassification,
    IncidentRequest,
    Severity,
)


class IncidentRepository:
    """Encapsulate SQLAlchemy queries and writes for incident records."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        incident: IncidentRequest,
        analysis: IncidentAnalysisResponse,
    ) -> IncidentRecord:
        """Stage a new incident record in the current transaction."""
        record = IncidentRecord(
            service=incident.service,
            error=incident.error,
            log=incident.log,
            requested_severity=(incident.severity.value if incident.severity else None),
            resolved_severity=analysis.severity.value,
            classification=analysis.classification.value,
            probable_cause=analysis.probable_cause,
            recommended_actions=analysis.recommended_actions,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def get(self, incident_id: int) -> IncidentRecord | None:
        """Return one incident by primary key."""
        return self.session.get(IncidentRecord, incident_id)

    def list(
        self,
        *,
        service: str | None,
        severity: Severity | None,
        classification: IncidentClassification | None,
        limit: int,
        offset: int,
    ) -> list[IncidentRecord]:
        """Return filtered incidents ordered newest first."""
        statement = select(IncidentRecord)
        if service is not None:
            statement = statement.where(IncidentRecord.service == service)
        if severity is not None:
            statement = statement.where(
                IncidentRecord.resolved_severity == severity.value
            )
        if classification is not None:
            statement = statement.where(
                IncidentRecord.classification == classification.value
            )
        statement = (
            statement.order_by(IncidentRecord.id.desc()).offset(offset).limit(limit)
        )
        return list(self.session.scalars(statement))
