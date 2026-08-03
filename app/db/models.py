"""SQLAlchemy mappings for persisted incidents."""

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.incident import MAX_ERROR_LENGTH, MAX_SERVICE_LENGTH


class IncidentRecord(Base):
    """An input incident and the deterministic analysis produced for it."""

    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint(
            "requested_severity IS NULL OR "
            "requested_severity IN ('low', 'medium', 'high', 'critical')",
            name="requested_severity_values",
        ),
        CheckConstraint(
            "resolved_severity IN ('low', 'medium', 'high', 'critical')",
            name="resolved_severity_values",
        ),
        CheckConstraint(
            "classification IN ('authentication_error', "
            "'database_connection_error', 'timeout_error', 'unknown_error')",
            name="classification_values",
        ),
        Index("ix_incidents_service", "service"),
        Index("ix_incidents_resolved_severity", "resolved_severity"),
        Index("ix_incidents_classification", "classification"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service: Mapped[str] = mapped_column(String(MAX_SERVICE_LENGTH), nullable=False)
    error: Mapped[str] = mapped_column(String(MAX_ERROR_LENGTH), nullable=False)
    log: Mapped[str] = mapped_column(Text, nullable=False)
    requested_severity: Mapped[str | None] = mapped_column(String(8), nullable=True)
    resolved_severity: Mapped[str] = mapped_column(String(8), nullable=False)
    classification: Mapped[str] = mapped_column(String(50), nullable=False)
    probable_cause: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_actions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"IncidentRecord(id={self.id!r}, service={self.service!r})"
