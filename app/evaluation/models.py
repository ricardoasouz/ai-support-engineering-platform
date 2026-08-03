"""Schemas for original golden cases and reproducible evaluation reports."""

from pydantic import BaseModel, ConfigDict, Field

from app.agent.models import AgentFinalResponse
from app.models.incident import IncidentClassification, Severity


class GoldenIncident(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    error: str
    log: str
    severity: Severity | None = None


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]+$")
    incident: GoldenIncident
    expected_classification: IncidentClassification
    expected_source_ids: list[str]
    required_tools: list[str]
    expect_escalation: bool
    max_steps: int = Field(ge=1)
    max_tool_calls: int = Field(ge=1)


class EvaluationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    classification: IncidentClassification
    final: AgentFinalResponse
    steps: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    tools_used: list[str]
    retrieved_source_ids: list[str]
    latency_ms: float = Field(ge=0)


class CaseMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    citation_validity: float = Field(ge=0, le=1)
    citation_precision: float = Field(ge=0, le=1)
    tool_selection: float = Field(ge=0, le=1)
    within_budgets: bool
    schema_valid: bool
    classification_correct: bool
    response_complete: bool
    escalation_correct: bool
    retrieval_relevance: float = Field(ge=0, le=1)
    no_fabricated_sources: bool
    latency_ms: float = Field(ge=0)


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    provider: str
    model: str
    cases: list[CaseMetrics]
    aggregate: dict[str, float]
