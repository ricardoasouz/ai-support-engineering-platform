"""Asynchronous resolution retrieval API tests."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import AIResolutionRecord, IncidentRecord


def create_incident(client: TestClient) -> int:
    response = client.post(
        "/api/v1/incidents",
        json={
            "service": "identity-api",
            "error": "JWT expired",
            "log": "Bearer token expired",
        },
    )
    assert response.status_code == 200
    incidents = client.get("/api/v1/incidents").json()
    return incidents[0]["id"]


def test_resolution_is_pending_before_worker_processing(client: TestClient) -> None:
    incident_id = create_incident(client)

    response = client.get(f"/api/v1/incidents/{incident_id}/resolution")

    assert response.status_code == 200
    assert response.json() == {
        "incident_id": incident_id,
        "status": "pending",
        "attempt_count": 0,
        "summary": None,
        "root_cause": None,
        "recommended_actions": None,
        "confidence": None,
        "cited_sources": None,
        "evidence_summary": None,
        "tools_used": None,
        "limitations": None,
        "escalation_required": None,
        "human_review_recommended": None,
        "llm_provider": None,
        "llm_model": None,
        "embedding_provider": None,
        "embedding_model": None,
        "prompt_name": None,
        "prompt_version": None,
        "error_message": None,
        "created_at": None,
        "updated_at": None,
        "completed_at": None,
    }


def test_completed_resolution_and_context_are_retrievable(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = create_incident(client)
    now = datetime.now(UTC)
    with session_factory() as session:
        assert session.scalar(
            select(IncidentRecord).where(IncidentRecord.id == incident_id)
        )
        session.add(
            AIResolutionRecord(
                incident_id=incident_id,
                event_id="c5ff1f13-27cb-4bd7-9ea7-45aba0524393",
                status="completed",
                attempt_count=1,
                summary="Expired JWT",
                root_cause="Token lifetime elapsed",
                recommended_actions=["Refresh the token"],
                confidence=0.9,
                cited_sources=[
                    {
                        "source_id": "runbook-jwt-authentication",
                        "chunk_id": 11,
                    }
                ],
                retrieved_context=[
                    {
                        "chunk_id": 11,
                        "source_id": "runbook-jwt-authentication",
                        "title": "JWT and Authentication Failures",
                        "category": "authentication",
                        "content": "Refresh expired tokens.",
                        "similarity": 0.93,
                    }
                ],
                llm_provider="ollama",
                llm_model="qwen2.5:1.5b-instruct",
                embedding_provider="ollama",
                embedding_model="nomic-embed-text:v1.5",
                completed_at=now,
                updated_at=now,
            )
        )
        session.commit()

    resolution = client.get(f"/api/v1/incidents/{incident_id}/resolution")
    context = client.get(f"/api/v1/incidents/{incident_id}/resolution/context")

    assert resolution.status_code == 200
    assert resolution.json()["status"] == "completed"
    assert resolution.json()["cited_sources"] == [
        {"source_id": "runbook-jwt-authentication", "chunk_id": 11}
    ]
    assert context.status_code == 200
    assert context.json()["context"][0]["similarity"] == 0.93


def test_resolution_returns_404_for_missing_incident(client: TestClient) -> None:
    response = client.get("/api/v1/incidents/999/resolution")
    context = client.get("/api/v1/incidents/999/resolution/context")

    assert response.status_code == 404
    assert context.status_code == 404
