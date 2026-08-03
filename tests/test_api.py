"""HTTP contract and validation tests."""

import pytest
from fastapi.testclient import TestClient

from app.models.incident import MAX_LOG_LENGTH

VALID_INCIDENT = {
    "service": "catalog-api",
    "error": "Unexpected processing failure",
    "log": "Worker exited while processing product update",
}


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "ai-support-engineering-platform",
    }


def test_incident_analysis_returns_unknown_and_honors_severity(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/incidents",
        json={
            **VALID_INCIDENT,
            "severity": "high",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["classification"] == "unknown_error"
    assert body["severity"] == "high"
    assert body["probable_cause"]
    assert len(body["recommended_actions"]) >= 1


@pytest.mark.parametrize("field", ["service", "error", "log"])
def test_required_incident_fields_cannot_be_blank(
    client: TestClient,
    field: str,
) -> None:
    response = client.post(
        "/api/v1/incidents",
        json={**VALID_INCIDENT, field: "   "},
    )

    assert response.status_code == 422
    assert response.json()["detail"]


def test_missing_required_field_is_rejected(client: TestClient) -> None:
    payload = VALID_INCIDENT.copy()
    del payload["log"]

    response = client.post(
        "/api/v1/incidents",
        json=payload,
    )

    assert response.status_code == 422


def test_invalid_severity_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/incidents",
        json={**VALID_INCIDENT, "severity": "urgent"},
    )

    assert response.status_code == 422


def test_undeclared_fields_are_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/incidents",
        json={**VALID_INCIDENT, "internal_notes": "must not be accepted"},
    )

    assert response.status_code == 422


def test_oversized_log_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/incidents",
        json={**VALID_INCIDENT, "log": "x" * (MAX_LOG_LENGTH + 1)},
    )

    assert response.status_code == 422
