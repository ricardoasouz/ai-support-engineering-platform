"""Strict generated-output and API-facing AI resolution models."""

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class AIResolutionStatus(str, Enum):
    """Durable lifecycle state for asynchronous incident resolution."""

    PENDING = "pending"
    PROCESSING = "processing"
    RETRYABLE = "retryable"
    COMPLETED = "completed"
    FAILED = "failed"


class SourceCitation(BaseModel):
    """Reference to an exact chunk supplied to the model."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=120)
    chunk_id: int = Field(gt=0)


class GeneratedResolution(BaseModel):
    """Schema enforced at the provider and validated again locally."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2_000)
    root_cause: str = Field(min_length=1, max_length=4_000)
    recommended_actions: list[Annotated[str, Field(min_length=1, max_length=1_000)]] = (
        Field(min_length=1, max_length=8)
    )
    confidence: float = Field(ge=0, le=1)
    cited_sources: list[SourceCitation] = Field(min_length=1, max_length=8)


class ResolutionResponse(BaseModel):
    """Durable resolution state returned by the incident API."""

    incident_id: int
    status: AIResolutionStatus
    attempt_count: int = Field(ge=0)
    summary: str | None = None
    root_cause: str | None = None
    recommended_actions: list[str] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    cited_sources: list[SourceCitation] | None = None
    evidence_summary: str | None = None
    tools_used: list[str] | None = None
    limitations: list[str] | None = None
    escalation_required: bool | None = None
    human_review_recommended: bool | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    prompt_name: str | None = None
    prompt_version: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class ResolutionContextItem(BaseModel):
    """Persisted retrieval context exposed for support diagnostics."""

    chunk_id: int
    source_id: str
    title: str
    category: str
    content: str
    similarity: float


class ResolutionContextResponse(BaseModel):
    """Context used by the last resolution attempt, without live inference."""

    incident_id: int
    status: AIResolutionStatus
    context: list[ResolutionContextItem]
