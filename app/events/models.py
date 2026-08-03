"""Versioned domain event envelopes."""

from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.incident import IncidentClassification, Severity

INCIDENT_CREATED_EVENT_TYPE = "incident.created"
INCIDENT_CREATED_EVENT_VERSION = 1


class IncidentCreatedEvent(BaseModel):
    """Minimal, versioned event emitted after an incident is persisted."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    event_type: Literal["incident.created"]
    event_version: Literal[1]
    occurred_at: datetime
    incident_id: int = Field(gt=0)
    service: str = Field(min_length=1, max_length=100)
    classification: IncidentClassification
    severity: Severity

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Require an unambiguous timestamp and normalize it to UTC."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(UTC)

    @classmethod
    def create(
        cls,
        *,
        incident_id: int,
        service: str,
        classification: IncidentClassification,
        severity: Severity,
        occurred_at: datetime | None = None,
    ) -> Self:
        """Build the current incident-created envelope."""
        return cls(
            event_id=uuid4(),
            event_type=INCIDENT_CREATED_EVENT_TYPE,
            event_version=INCIDENT_CREATED_EVENT_VERSION,
            occurred_at=occurred_at or datetime.now(UTC),
            incident_id=incident_id,
            service=service,
            classification=classification,
            severity=severity,
        )

    def serialize(self) -> bytes:
        """Serialize the event as UTF-8 JSON for Kafka."""
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def deserialize(cls, value: bytes | str) -> Self:
        """Validate and deserialize a Kafka message value."""
        return cls.model_validate_json(value)
