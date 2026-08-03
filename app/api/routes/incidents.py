"""Incident analysis and retrieval endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

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
from app.repositories.incidents import IncidentRepository
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
