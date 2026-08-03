"""Incident analysis endpoints."""

from fastapi import APIRouter, status

from app.models.incident import IncidentAnalysisResponse, IncidentRequest
from app.services.analyzer import analyze_incident

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.post(
    "",
    response_model=IncidentAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze an incident",
    response_description="Deterministic incident analysis",
)
def analyze_incident_report(incident: IncidentRequest) -> IncidentAnalysisResponse:
    """Analyze an incident using the deterministic Phase 1 rules."""
    return analyze_incident(incident)
