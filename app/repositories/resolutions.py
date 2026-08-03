"""Persistence access for asynchronous incident resolutions."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AIResolutionRecord


class ResolutionRepository:
    """Read durable resolution state without invoking an AI provider."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_incident(self, incident_id: int) -> AIResolutionRecord | None:
        """Return the single resolution state for an incident, when present."""
        return self.session.scalar(
            select(AIResolutionRecord).where(
                AIResolutionRecord.incident_id == incident_id
            )
        )
