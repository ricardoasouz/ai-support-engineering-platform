"""Incident API request and response models."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

MAX_SERVICE_LENGTH = 100
MAX_ERROR_LENGTH = 1_000
MAX_LOG_LENGTH = 20_000


class Severity(str, Enum):
    """Supported incident severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentClassification(str, Enum):
    """Classifications produced by the deterministic analyzer."""

    AUTHENTICATION_ERROR = "authentication_error"
    DATABASE_CONNECTION_ERROR = "database_connection_error"
    TIMEOUT_ERROR = "timeout_error"
    UNKNOWN_ERROR = "unknown_error"


class IncidentRequest(BaseModel):
    """Incident information supplied for analysis."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "service": "identity-api",
                    "error": "JWT validation failed",
                    "log": "Bearer token has expired for subject 42",
                    "severity": "high",
                }
            ]
        },
    )

    service: str = Field(
        min_length=1,
        max_length=MAX_SERVICE_LENGTH,
        description="Name of the affected service.",
    )
    error: str = Field(
        min_length=1,
        max_length=MAX_ERROR_LENGTH,
        description="Short error message or summary.",
    )
    log: str = Field(
        min_length=1,
        max_length=MAX_LOG_LENGTH,
        description="Relevant log excerpt used for deterministic analysis.",
    )
    severity: Severity | None = Field(
        default=None,
        description="Optional caller-assigned severity; overrides the analyzer default.",
    )


class IncidentAnalysisResponse(BaseModel):
    """Deterministic analysis returned for an incident."""

    classification: IncidentClassification
    severity: Severity
    probable_cause: str
    recommended_actions: list[str] = Field(min_length=1)
