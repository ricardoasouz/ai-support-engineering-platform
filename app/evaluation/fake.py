"""Deterministic candidate generator for broker- and provider-free CI evaluation."""

from app.agent.models import AgentFinalResponse
from app.ai.models import SourceCitation
from app.evaluation.models import EvaluationCandidate, GoldenCase
from app.models.incident import IncidentRequest
from app.services.analyzer import analyze_incident


def golden_fake_candidate(case: GoldenCase) -> EvaluationCandidate:
    """Produce a stable schema-valid candidate using deterministic Phase 1 analysis."""
    analysis = analyze_incident(
        IncidentRequest.model_validate(case.incident.model_dump())
    )
    content = f"{case.incident.error} {case.incident.log}".lower()
    source_ids = {
        "authentication_error": ["runbook-jwt-authentication"],
        "database_connection_error": ["runbook-postgresql-connectivity"],
        "timeout_error": ["runbook-http-timeouts"],
    }.get(analysis.classification.value, [])
    if not source_ids and any(
        term in content for term in ("dns", "network", "name resolution")
    ):
        source_ids = ["runbook-network-connectivity"]
    elif not source_ids and "container" in content:
        source_ids = ["runbook-container-availability"]
    tools_used = ["get_incident", "retrieve_runbooks"]
    cited_sources = [
        SourceCitation(source_id=source_id, chunk_id=index + 1)
        for index, source_id in enumerate(source_ids)
    ]
    return EvaluationCandidate(
        case_id=case.case_id,
        classification=analysis.classification,
        final=AgentFinalResponse(
            summary=f"Deterministic evaluation summary for {case.case_id}.",
            root_cause=analysis.probable_cause,
            recommended_actions=analysis.recommended_actions,
            confidence=0.8 if source_ids else 0.4,
            cited_sources=cited_sources,
            evidence_summary="Evidence was selected from the expected local runbook set.",
            tools_used=tools_used,
            limitations=[] if source_ids else ["Runbook evidence is insufficient."],
            escalation_required=not source_ids,
            human_review_recommended=True,
        ),
        steps=len(tools_used) + 1,
        tool_calls=len(tools_used),
        tools_used=tools_used,
        retrieved_source_ids=source_ids,
        latency_ms=0,
    )
