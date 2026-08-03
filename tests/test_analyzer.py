"""Unit tests for deterministic analyzer behavior."""

import pytest

from app.models.incident import (
    IncidentClassification,
    IncidentRequest,
    Severity,
)
from app.services.analyzer import analyze_incident


@pytest.mark.parametrize(
    ("error", "log", "expected_classification", "expected_severity"),
    [
        (
            "JWT validation failed",
            "Bearer token has expired for subject 42",
            IncidentClassification.AUTHENTICATION_ERROR,
            Severity.HIGH,
        ),
        (
            "Database connection failed",
            "Could not connect to PostgreSQL: connection refused",
            IncidentClassification.DATABASE_CONNECTION_ERROR,
            Severity.CRITICAL,
        ),
        (
            "Upstream request timed out",
            "Payment provider deadline exceeded after 30 seconds",
            IncidentClassification.TIMEOUT_ERROR,
            Severity.MEDIUM,
        ),
        (
            "Unexpected processing failure",
            "Worker exited while processing product update",
            IncidentClassification.UNKNOWN_ERROR,
            Severity.MEDIUM,
        ),
    ],
)
def test_incident_classification_rules(
    error: str,
    log: str,
    expected_classification: IncidentClassification,
    expected_severity: Severity,
) -> None:
    incident = IncidentRequest(service="test-api", error=error, log=log)

    result = analyze_incident(incident)

    assert result.classification is expected_classification
    assert result.severity is expected_severity
    assert result.probable_cause
    assert result.recommended_actions


def test_sqlalchemy_connection_trace_is_classified_as_database_error() -> None:
    incident = IncidentRequest(
        service="orders-api",
        error="sqlalchemy.exc.OperationalError",
        log=(
            "(psycopg.OperationalError) connection to server at db.internal failed: "
            "Connection refused"
        ),
    )

    result = analyze_incident(incident)

    assert result.classification is IncidentClassification.DATABASE_CONNECTION_ERROR


def test_generic_connection_refusal_is_not_assumed_to_be_a_database_error() -> None:
    incident = IncidentRequest(
        service="catalog-api",
        error="Upstream dependency unavailable",
        log="Connection refused while calling the inventory service",
    )

    result = analyze_incident(incident)

    assert result.classification is IncidentClassification.UNKNOWN_ERROR


@pytest.mark.parametrize(
    ("error", "log", "expected_classification"),
    [
        (
            "JWT rejected during database connection timeout",
            "Authentication failed after deadline exceeded",
            IncidentClassification.AUTHENTICATION_ERROR,
        ),
        (
            "Database connection timed out",
            "Query deadline exceeded",
            IncidentClassification.DATABASE_CONNECTION_ERROR,
        ),
    ],
)
def test_rule_precedence_is_stable(
    error: str,
    log: str,
    expected_classification: IncidentClassification,
) -> None:
    incident = IncidentRequest(service="test-api", error=error, log=log)

    result = analyze_incident(incident)

    assert result.classification is expected_classification


def test_caller_severity_overrides_rule_default() -> None:
    incident = IncidentRequest(
        service="identity-api",
        error="JWT expired",
        log="Token has expired",
        severity=Severity.LOW,
    )

    result = analyze_incident(incident)

    assert result.classification is IncidentClassification.AUTHENTICATION_ERROR
    assert result.severity is Severity.LOW
