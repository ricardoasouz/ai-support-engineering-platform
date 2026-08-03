"""Rule-based incident log analyzer."""

import re
from dataclasses import dataclass

from app.models.incident import (
    IncidentAnalysisResponse,
    IncidentClassification,
    IncidentRequest,
    Severity,
)


@dataclass(frozen=True)
class AnalysisRule:
    """A deterministic classification rule and its response content."""

    classification: IncidentClassification
    default_severity: Severity
    patterns: tuple[re.Pattern[str], ...]
    probable_cause: str
    recommended_actions: tuple[str, ...]

    def matches(self, content: str) -> bool:
        """Return true when any pattern occurs in the incident content."""
        return any(pattern.search(content) for pattern in self.patterns)


RULES: tuple[AnalysisRule, ...] = (
    AnalysisRule(
        classification=IncidentClassification.AUTHENTICATION_ERROR,
        default_severity=Severity.HIGH,
        patterns=(
            re.compile(r"\bjwt\b", re.IGNORECASE),
            re.compile(r"\bjson web token\b", re.IGNORECASE),
            re.compile(
                r"\b(?:invalid|expired|malformed) (?:access )?token\b", re.IGNORECASE
            ),
            re.compile(r"\btoken (?:has )?expired\b", re.IGNORECASE),
            re.compile(r"\bauthentication (?:failed|error|failure)\b", re.IGNORECASE),
            re.compile(r"\bunauthori[sz]ed\b", re.IGNORECASE),
            re.compile(r"\bbearer token\b", re.IGNORECASE),
            re.compile(r"\bsignature verification failed\b", re.IGNORECASE),
            re.compile(r"\b401\b"),
        ),
        probable_cause=(
            "The request could not be authenticated because its credentials or token "
            "were missing, invalid, expired, or failed verification."
        ),
        recommended_actions=(
            "Verify that the client sends a valid bearer token.",
            "Check token expiry, issuer, audience, and signing-key configuration.",
            "Review authentication service logs for rejected credentials.",
        ),
    ),
    AnalysisRule(
        classification=IncidentClassification.DATABASE_CONNECTION_ERROR,
        default_severity=Severity.CRITICAL,
        patterns=(
            re.compile(r"\bdatabase connection\b", re.IGNORECASE),
            re.compile(
                r"\b(?:could not|unable to|failed to) connect to "
                r"(?:the )?(?:database|postgres(?:ql)?|mysql|mariadb)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"(?=.*\b(?:database|postgres(?:ql)?|mysql|mariadb|oracle|sql server)\b)"
                r"(?=.*\bconnection (?:refused|reset)\b)",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"\bconnection pool (?:exhausted|timeout|timed out)\b", re.IGNORECASE
            ),
            re.compile(
                r"(?=.*\b(?:psycopg(?:2)?|sqlalchemy\.exc)\."
                r"(?:operationalerror|interfaceerror)\b)"
                r"(?=.*\b(?:connection refused|could not connect|server closed the connection)\b)",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(r"\btoo many connections\b", re.IGNORECASE),
        ),
        probable_cause=(
            "The application could not establish or retain a database connection, "
            "possibly because the database is unavailable, unreachable, or out of connections."
        ),
        recommended_actions=(
            "Confirm that the database service is running and reachable.",
            "Validate the connection settings and credentials.",
            "Inspect database and connection-pool capacity and logs.",
        ),
    ),
    AnalysisRule(
        classification=IncidentClassification.TIMEOUT_ERROR,
        default_severity=Severity.MEDIUM,
        patterns=(
            re.compile(r"\btime(?:d)?[ -]?out\b", re.IGNORECASE),
            re.compile(r"\btimeout(?:error|exception)?\b", re.IGNORECASE),
            re.compile(r"\bdeadline exceeded\b", re.IGNORECASE),
            re.compile(r"\bgateway timeout\b", re.IGNORECASE),
            re.compile(r"\b504\b"),
        ),
        probable_cause=(
            "An operation exceeded its allowed response time, likely because a dependency "
            "was slow, unavailable, or the configured deadline was too short."
        ),
        recommended_actions=(
            "Identify the timed-out dependency and check its health and latency.",
            "Review timeout thresholds against expected operation duration.",
            "Inspect resource saturation and recent latency changes.",
        ),
    ),
)

UNKNOWN_CAUSE = (
    "The supplied error and log do not match a known Phase 1 classification rule."
)
UNKNOWN_ACTIONS = (
    "Review the complete application logs and stack trace.",
    "Correlate the incident with recent deployments or configuration changes.",
    "Add a new deterministic rule if this failure pattern recurs.",
)


def analyze_incident(incident: IncidentRequest) -> IncidentAnalysisResponse:
    """Classify an incident with ordered deterministic rules.

    Authentication rules take precedence over database rules, which take precedence
    over general timeout rules. A caller-supplied severity overrides the rule default.
    """
    content = f"{incident.error}\n{incident.log}"

    for rule in RULES:
        if rule.matches(content):
            return IncidentAnalysisResponse(
                classification=rule.classification,
                severity=incident.severity or rule.default_severity,
                probable_cause=rule.probable_cause,
                recommended_actions=list(rule.recommended_actions),
            )

    return IncidentAnalysisResponse(
        classification=IncidentClassification.UNKNOWN_ERROR,
        severity=incident.severity or Severity.MEDIUM,
        probable_cause=UNKNOWN_CAUSE,
        recommended_actions=list(UNKNOWN_ACTIONS),
    )
