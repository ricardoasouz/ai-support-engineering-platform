"""Domain-event schema and serialization tests."""

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.events.models import IncidentCreatedEvent
from app.models.incident import IncidentClassification, Severity


def build_event() -> IncidentCreatedEvent:
    """Return a stable current-version event for tests."""
    return IncidentCreatedEvent.create(
        incident_id=42,
        service="identity-api",
        classification=IncidentClassification.AUTHENTICATION_ERROR,
        severity=Severity.HIGH,
        occurred_at=datetime(2026, 8, 3, 12, 30, tzinfo=UTC),
    )


def test_event_round_trip_is_versioned_and_minimal() -> None:
    event = build_event()

    serialized = event.serialize()
    restored = IncidentCreatedEvent.deserialize(serialized)
    payload = json.loads(serialized)

    assert restored == event
    assert UUID(payload["event_id"]) == event.event_id
    assert payload["event_type"] == "incident.created"
    assert payload["event_version"] == 1
    assert payload["incident_id"] == 42
    assert payload["classification"] == "authentication_error"
    assert payload["severity"] == "high"
    assert "error" not in payload
    assert "log" not in payload


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_type", "incident.updated"),
        ("event_version", 2),
        ("incident_id", 0),
        ("occurred_at", "2026-08-03T12:30:00"),
    ],
)
def test_event_schema_rejects_invalid_envelope(field: str, value: object) -> None:
    payload = build_event().model_dump(mode="json")
    payload[field] = value

    with pytest.raises(ValidationError):
        IncidentCreatedEvent.model_validate(payload)


def test_event_schema_rejects_unknown_fields() -> None:
    payload = build_event().model_dump(mode="json")
    payload["log"] = "raw logs must not be published"

    with pytest.raises(ValidationError):
        IncidentCreatedEvent.model_validate(payload)
