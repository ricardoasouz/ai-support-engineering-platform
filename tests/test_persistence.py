"""Incident persistence, retrieval, and filtering tests."""

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import IncidentRecord


def create_incident(
    client: TestClient,
    *,
    service: str,
    error: str,
    log: str,
    severity: str | None = None,
) -> None:
    """Submit one valid incident and assert analysis succeeds."""
    payload = {"service": service, "error": error, "log": log}
    if severity is not None:
        payload["severity"] = severity
    response = client.post("/api/v1/incidents", json=payload)
    assert response.status_code == 200


def test_analysis_persists_input_and_result(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    create_incident(
        client,
        service="identity-api",
        error="JWT validation failed",
        log="Bearer token has expired",
        severity="low",
    )

    with session_factory() as session:
        record = session.scalar(select(IncidentRecord))

    assert record is not None
    assert record.service == "identity-api"
    assert record.error == "JWT validation failed"
    assert record.log == "Bearer token has expired"
    assert record.requested_severity == "low"
    assert record.resolved_severity == "low"
    assert record.classification == "authentication_error"
    assert record.probable_cause
    assert len(record.recommended_actions) == 3
    assert record.created_at is not None


def test_list_and_get_return_persisted_incident(client: TestClient) -> None:
    create_incident(
        client,
        service="orders-api",
        error="Database connection failed",
        log="Could not connect to PostgreSQL: connection refused",
    )

    list_response = client.get("/api/v1/incidents")
    assert list_response.status_code == 200
    incidents = list_response.json()
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident["service"] == "orders-api"
    assert incident["requested_severity"] is None
    assert incident["resolved_severity"] == "critical"
    assert incident["classification"] == "database_connection_error"
    assert incident["created_at"]

    get_response = client.get(f"/api/v1/incidents/{incident['id']}")
    assert get_response.status_code == 200
    assert get_response.json() == incident


def test_missing_incident_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/incidents/9999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Incident not found"}


def test_list_filters_by_service_resolved_severity_and_classification(
    client: TestClient,
) -> None:
    create_incident(
        client,
        service="identity-api",
        error="JWT expired",
        log="Authentication failed",
    )
    create_incident(
        client,
        service="orders-api",
        error="Database connection failed",
        log="Could not connect to PostgreSQL: connection refused",
    )
    create_incident(
        client,
        service="orders-api",
        error="Upstream timed out",
        log="Gateway timeout",
        severity="low",
    )

    service_results = client.get(
        "/api/v1/incidents", params={"service": "orders-api"}
    ).json()
    severity_results = client.get(
        "/api/v1/incidents", params={"severity": "low"}
    ).json()
    classification_results = client.get(
        "/api/v1/incidents",
        params={"classification": "authentication_error"},
    ).json()

    assert len(service_results) == 2
    assert {item["service"] for item in service_results} == {"orders-api"}
    assert len(severity_results) == 1
    assert severity_results[0]["classification"] == "timeout_error"
    assert len(classification_results) == 1
    assert classification_results[0]["service"] == "identity-api"


def test_list_pagination_is_deterministic(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    for service in ("first-api", "second-api", "third-api"):
        create_incident(
            client,
            service=service,
            error="Unexpected failure",
            log="No known pattern",
        )

    response = client.get("/api/v1/incidents", params={"limit": 1, "offset": 1})

    assert response.status_code == 200
    assert [item["service"] for item in response.json()] == ["second-api"]
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(IncidentRecord)) == 3
