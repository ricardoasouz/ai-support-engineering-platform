"""Incident analysis and retrieval endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.agent.models import (
    AgentExecution,
    AgentStatus,
    AgentStepResponse,
    FeedbackRequest,
    FeedbackResponse,
)
from app.ai.models import (
    AIResolutionStatus,
    ResolutionContextItem,
    ResolutionContextResponse,
    ResolutionResponse,
    SourceCitation,
)
from app.db.session import get_db
from app.events.dependencies import get_outbox_dispatcher
from app.events.dispatcher import OutboxDispatcher
from app.models.incident import (
    MAX_SERVICE_LENGTH,
    IncidentAnalysisResponse,
    IncidentClassification,
    IncidentRequest,
    IncidentResponse,
    Severity,
)
from app.observability.metrics import get_metrics
from app.observability.tracing import mark_span_error, start_span
from app.repositories.agent import AgentRepository
from app.repositories.incidents import IncidentRepository
from app.repositories.resolutions import ResolutionRepository
from app.services.incidents import analyze_and_persist_incident

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.post(
    "",
    response_model=IncidentAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze an incident",
    response_description="Deterministic incident analysis",
)
def analyze_incident_report(
    incident: IncidentRequest,
    session: Annotated[Session, Depends(get_db)],
    dispatcher: Annotated[OutboxDispatcher, Depends(get_outbox_dispatcher)],
) -> IncidentAnalysisResponse:
    """Analyze and persist an incident using deterministic rules."""
    return analyze_and_persist_incident(incident, session, dispatcher)


@router.get(
    "",
    response_model=list[IncidentResponse],
    status_code=status.HTTP_200_OK,
    summary="List incidents",
)
def list_incidents(
    session: Annotated[Session, Depends(get_db)],
    service: Annotated[
        str | None,
        Query(min_length=1, max_length=MAX_SERVICE_LENGTH),
    ] = None,
    severity: Severity | None = None,
    classification: IncidentClassification | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IncidentResponse]:
    """Return persisted incidents, newest first, with optional exact filters."""
    records = IncidentRepository(session).list(
        service=service,
        severity=severity,
        classification=classification,
        limit=limit,
        offset=offset,
    )
    return [IncidentResponse.model_validate(record) for record in records]


@router.get(
    "/{incident_id}",
    response_model=IncidentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get an incident",
)
def get_incident(
    incident_id: int,
    session: Annotated[Session, Depends(get_db)],
) -> IncidentResponse:
    """Return a persisted incident or a 404 response."""
    record = IncidentRepository(session).get(incident_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        )
    return IncidentResponse.model_validate(record)


def _require_incident(incident_id: int, session: Session) -> None:
    if IncidentRepository(session).get(incident_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        )


@router.get(
    "/{incident_id}/resolution",
    response_model=ResolutionResponse,
    status_code=status.HTTP_200_OK,
    summary="Get asynchronous AI resolution state",
)
def get_incident_resolution(
    incident_id: int,
    session: Annotated[Session, Depends(get_db)],
) -> ResolutionResponse:
    """Return durable state only; this endpoint never runs model inference."""
    _require_incident(incident_id, session)
    record = ResolutionRepository(session).get_for_incident(incident_id)
    if record is None:
        return ResolutionResponse(
            incident_id=incident_id,
            status=AIResolutionStatus.PENDING,
            attempt_count=0,
        )
    citations = (
        [SourceCitation.model_validate(item) for item in record.cited_sources]
        if record.cited_sources
        else None
    )
    return ResolutionResponse(
        incident_id=incident_id,
        status=record.status,
        attempt_count=record.attempt_count,
        summary=record.summary,
        root_cause=record.root_cause,
        recommended_actions=record.recommended_actions,
        confidence=record.confidence,
        cited_sources=citations,
        evidence_summary=record.evidence_summary,
        tools_used=record.tools_used,
        limitations=record.limitations,
        escalation_required=record.escalation_required,
        human_review_recommended=record.human_review_recommended,
        llm_provider=record.llm_provider,
        llm_model=record.llm_model,
        embedding_provider=record.embedding_provider,
        embedding_model=record.embedding_model,
        prompt_name=record.prompt_name,
        prompt_version=record.prompt_version,
        error_message=record.error_message,
        created_at=record.created_at,
        updated_at=record.updated_at,
        completed_at=record.completed_at,
    )


@router.get(
    "/{incident_id}/resolution/context",
    response_model=ResolutionContextResponse,
    status_code=status.HTTP_200_OK,
    summary="Get retrieval context used by the resolution",
)
def get_incident_resolution_context(
    incident_id: int,
    session: Annotated[Session, Depends(get_db)],
) -> ResolutionContextResponse:
    """Return persisted retrieval evidence without calling embeddings or an LLM."""
    _require_incident(incident_id, session)
    record = ResolutionRepository(session).get_for_incident(incident_id)
    if record is None:
        return ResolutionContextResponse(
            incident_id=incident_id,
            status=AIResolutionStatus.PENDING,
            context=[],
        )
    context = [
        ResolutionContextItem.model_validate(item)
        for item in (record.retrieved_context or [])
    ]
    return ResolutionContextResponse(
        incident_id=incident_id,
        status=record.status,
        context=context,
    )


@router.get(
    "/{incident_id}/agent-execution",
    response_model=AgentExecution,
    summary="Get controlled agent execution metadata",
)
def get_agent_execution(
    incident_id: int,
    session: Annotated[Session, Depends(get_db)],
) -> AgentExecution:
    """Return safe execution metadata without prompts or hidden reasoning."""
    _require_incident(incident_id, session)
    execution = AgentRepository(session).get_execution(incident_id)
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent execution not found",
        )
    return AgentExecution.model_validate(execution)


@router.get(
    "/{incident_id}/agent-execution/steps",
    response_model=list[AgentStepResponse],
    summary="List sanitized agent audit steps",
)
def list_agent_steps(
    incident_id: int,
    session: Annotated[Session, Depends(get_db)],
) -> list[AgentStepResponse]:
    """Return selected tools, sanitized arguments/results, evidence, and outcomes."""
    _require_incident(incident_id, session)
    repository = AgentRepository(session)
    execution = repository.get_execution(incident_id)
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent execution not found",
        )
    return [
        AgentStepResponse.model_validate(record)
        for record in repository.list_steps(execution.execution_id)
    ]


def _record_feedback(
    incident_id: int,
    feedback: FeedbackRequest,
    session: Session,
    *,
    outcome: str,
) -> FeedbackResponse:
    with start_span(
        "human review transition",
        attributes={"incident.id": incident_id, "review.outcome": outcome},
    ) as review_span:
        try:
            response = _record_feedback_impl(
                incident_id, feedback, session, outcome=outcome
            )
        except HTTPException as exc:
            if exc.status_code == status.HTTP_409_CONFLICT:
                get_metrics().count("review_conflicts", attributes={"outcome": outcome})
            mark_span_error(review_span, exc)
            raise
        review_span.set_attribute("resolution.id", response.resolution_id)
        get_metrics().count("review_feedback", attributes={"outcome": outcome})
        if outcome == "approved":
            get_metrics().count("review_approved")
            get_metrics().count("agent_review_approved")
        elif outcome == "rejected":
            get_metrics().count("review_rejected")
            get_metrics().count("agent_review_rejected")
        return response


def _record_feedback_impl(
    incident_id: int,
    feedback: FeedbackRequest,
    session: Session,
    *,
    outcome: str,
) -> FeedbackResponse:
    _require_incident(incident_id, session)
    repository = AgentRepository(session)
    execution = repository.get_execution(incident_id)
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No agent resolution is available for review",
        )
    if (
        outcome in {"approved", "rejected"}
        and execution.status != AgentStatus.AWAITING_REVIEW
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Resolution review is not pending; current status is {execution.status}",
        )
    record = repository.add_feedback(incident_id, feedback, outcome=outcome)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No agent resolution is available for review",
        )
    return FeedbackResponse.model_validate(record)


@router.post(
    "/{incident_id}/resolution/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record demo human feedback",
)
def create_resolution_feedback(
    incident_id: int,
    feedback: FeedbackRequest,
    session: Annotated[Session, Depends(get_db)],
) -> FeedbackResponse:
    """Persist bounded feedback; it is never used for automatic learning."""
    return _record_feedback(incident_id, feedback, session, outcome="feedback")


@router.post(
    "/{incident_id}/resolution/approve",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Approve a pending agent resolution",
)
def approve_resolution(
    incident_id: int,
    feedback: FeedbackRequest,
    session: Annotated[Session, Depends(get_db)],
) -> FeedbackResponse:
    return _record_feedback(incident_id, feedback, session, outcome="approved")


@router.post(
    "/{incident_id}/resolution/reject",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Reject a pending agent resolution",
)
def reject_resolution(
    incident_id: int,
    feedback: FeedbackRequest,
    session: Annotated[Session, Depends(get_db)],
) -> FeedbackResponse:
    return _record_feedback(incident_id, feedback, session, outcome="rejected")
