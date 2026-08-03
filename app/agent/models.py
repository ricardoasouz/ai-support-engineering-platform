"""Strict domain models for bounded and auditable agent execution."""

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai.models import SourceCitation


class AgentStatus(str, Enum):
    """Supported durable execution and human-review states."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_TOOL = "waiting_for_tool"
    COMPLETED = "completed"
    RETRYABLE = "retryable"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class AgentRequest(BaseModel):
    """Minimal durable request that starts one incident agent execution."""

    model_config = ConfigDict(extra="forbid")

    incident_id: int = Field(gt=0)
    event_id: UUID


class ToolRequest(BaseModel):
    """Planner-requested call before registry and argument validation."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, object] = Field(default_factory=dict)


class EvidenceReference(BaseModel):
    """Stable reference to evidence returned by an approved tool."""

    model_config = ConfigDict(extra="forbid")

    evidence_type: Literal["incident", "runbook", "similar_incident", "resolution"]
    reference_id: str = Field(min_length=1, max_length=160)
    source_id: str | None = Field(default=None, max_length=120)
    chunk_id: int | None = Field(default=None, gt=0)


class ToolResult(BaseModel):
    """Sanitized, bounded result from one server-controlled tool."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=80)
    status: Literal["succeeded", "rejected", "failed", "timed_out"]
    summary: str = Field(min_length=1, max_length=2_000)
    data: dict[str, object] = Field(default_factory=dict)
    evidence_references: list[EvidenceReference] = Field(
        default_factory=list, max_length=20
    )
    duration_ms: float = Field(ge=0)


class PlannerDecision(BaseModel):
    """Bounded structured decision; this is not private chain-of-thought."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=500)
    next_action: Literal["tool", "final"]
    tool_name: str | None = Field(default=None, max_length=80)
    tool_arguments: dict[str, object] | None = None
    reason_summary: str = Field(min_length=1, max_length=500)
    expected_evidence: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_action_shape(self) -> "PlannerDecision":
        """Require tool fields only for tool decisions."""
        if self.next_action == "tool":
            if not self.tool_name or self.tool_arguments is None:
                raise ValueError("Tool decisions require tool_name and tool_arguments")
        elif self.tool_name is not None or self.tool_arguments is not None:
            raise ValueError("Final decisions must not include tool fields")
        return self


class AgentFinalResponse(BaseModel):
    """Strict user-visible outcome grounded in gathered evidence."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2_000)
    root_cause: str = Field(min_length=1, max_length=4_000)
    recommended_actions: list[Annotated[str, Field(min_length=1, max_length=1_000)]] = (
        Field(min_length=1, max_length=8)
    )
    confidence: float = Field(ge=0, le=1)
    cited_sources: list[SourceCitation] = Field(max_length=8)
    evidence_summary: str = Field(min_length=1, max_length=4_000)
    tools_used: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        min_length=1, max_length=12
    )
    limitations: list[Annotated[str, Field(min_length=1, max_length=1_000)]] = Field(
        default_factory=list, max_length=8
    )
    escalation_required: bool
    human_review_recommended: bool


class AgentStep(BaseModel):
    """One auditable action persisted without hidden reasoning."""

    model_config = ConfigDict(extra="forbid")

    step_number: int = Field(gt=0)
    action_type: Literal["tool", "final"]
    tool_request: ToolRequest | None = None
    tool_result: ToolResult | None = None
    reason_summary: str = Field(min_length=1, max_length=500)
    expected_evidence: str = Field(min_length=1, max_length=500)
    outcome: str = Field(min_length=1, max_length=80)
    created_at: datetime


class AgentState(BaseModel):
    """In-memory bounded loop state reconstructed from durable steps."""

    model_config = ConfigDict(extra="forbid")

    request: AgentRequest
    status: AgentStatus = AgentStatus.PENDING
    steps: list[AgentStep] = Field(default_factory=list)
    tool_call_count: int = Field(default=0, ge=0)
    model_call_count: int = Field(default=0, ge=0)
    evidence_references: list[EvidenceReference] = Field(default_factory=list)


class AgentExecution(BaseModel):
    """API-facing execution metadata and safe error state."""

    model_config = ConfigDict(from_attributes=True)

    execution_id: UUID
    incident_id: int
    event_id: UUID
    status: AgentStatus
    provider: str
    model: str
    planner_prompt_name: str
    planner_prompt_version: str
    resolver_prompt_name: str
    resolver_prompt_version: str
    step_count: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    model_call_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    retrieved_chunk_count: int = Field(ge=0)
    total_duration_ms: float | None = Field(default=None, ge=0)
    provider_duration_ms: float = Field(ge=0)
    retrieval_duration_ms: float = Field(ge=0)
    tool_duration_ms: float = Field(ge=0)
    approximate_input_chars: int = Field(ge=0)
    approximate_output_chars: int = Field(ge=0)
    provider_usage: dict[str, object] | None = None
    error_type: str | None = None
    error_message_safe: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AgentStepResponse(BaseModel):
    """Persisted execution step exposed for audit and review."""

    model_config = ConfigDict(from_attributes=True)

    step_number: int
    action_type: str
    tool_name: str | None
    sanitized_arguments: dict[str, object] | None
    result_summary: dict[str, object] | None
    evidence_references: list[dict[str, object]]
    reason_summary: str
    expected_evidence: str
    outcome: str
    duration_ms: float
    created_at: datetime


class FeedbackRequest(BaseModel):
    """Bounded human-review metadata; reviewer is demo-only identity text."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2_000)
    reviewer: str = Field(min_length=1, max_length=120)


class FeedbackResponse(BaseModel):
    """Stored human feedback entry."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
    resolution_id: int
    rating: int | None
    outcome: str
    accepted: bool
    edited: bool
    comment: str | None
    reviewer: str
    created_at: datetime
