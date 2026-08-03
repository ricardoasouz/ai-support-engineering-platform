"""Agent audit and explicit demo human-review API tests."""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import AgentExecutionRecord, AIResolutionRecord


def create_reviewable_resolution(
    client: TestClient, session_factory: sessionmaker[Session]
) -> int:
    response = client.post(
        "/api/v1/incidents",
        json={
            "service": "identity-api",
            "error": "JWT expired",
            "log": "Bearer token expired",
        },
    )
    assert response.status_code == 200
    incident_id = client.get("/api/v1/incidents").json()[0]["id"]
    event_id = str(uuid4())
    with session_factory() as session:
        session.add(
            AIResolutionRecord(
                incident_id=incident_id,
                event_id=event_id,
                status="completed",
                attempt_count=1,
                summary="Expired JWT",
                root_cause="Token lifetime elapsed",
                recommended_actions=["Refresh token"],
                confidence=0.9,
                cited_sources=[],
                retrieved_context=[],
                evidence_summary="Incident metadata supports token expiry.",
                tools_used=["get_incident"],
                limitations=["No runbook citation in this fixture."],
                escalation_required=True,
                human_review_recommended=True,
                prompt_name="resolver",
                prompt_version="v1",
            )
        )
        session.add(
            AgentExecutionRecord(
                execution_id=str(uuid4()),
                incident_id=incident_id,
                event_id=event_id,
                status="awaiting_review",
                provider="fake",
                model="test-model",
                planner_prompt_name="planner",
                planner_prompt_version="v1",
                resolver_prompt_name="resolver",
                resolver_prompt_version="v1",
            )
        )
        session.commit()
    return incident_id


def test_execution_audit_and_feedback_approval(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    incident_id = create_reviewable_resolution(client, session_factory)

    execution = client.get(f"/api/v1/incidents/{incident_id}/agent-execution")
    steps = client.get(f"/api/v1/incidents/{incident_id}/agent-execution/steps")
    feedback = client.post(
        f"/api/v1/incidents/{incident_id}/resolution/feedback",
        json={"rating": 4, "comment": "Useful evidence", "reviewer": "demo-reviewer"},
    )
    approval = client.post(
        f"/api/v1/incidents/{incident_id}/resolution/approve",
        json={"rating": 5, "comment": "Approved", "reviewer": "demo-reviewer"},
    )

    assert execution.status_code == 200
    assert execution.json()["status"] == "awaiting_review"
    assert "prompt" not in execution.text.lower() or "prompt_version" in execution.text
    assert steps.status_code == 200 and steps.json() == []
    assert feedback.status_code == 201
    assert feedback.json()["outcome"] == "feedback"
    assert approval.status_code == 201
    assert approval.json()["accepted"] is True
    with session_factory() as session:
        stored = session.scalar(select(AgentExecutionRecord))
    assert stored is not None and stored.status == "approved"


def test_reject_requires_pending_review(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    incident_id = create_reviewable_resolution(client, session_factory)
    request = {"reviewer": "demo-reviewer", "comment": "Needs revision"}

    first = client.post(
        f"/api/v1/incidents/{incident_id}/resolution/reject", json=request
    )
    second = client.post(
        f"/api/v1/incidents/{incident_id}/resolution/reject", json=request
    )

    assert first.status_code == 201
    assert first.json()["outcome"] == "rejected"
    assert second.status_code == 409


def test_missing_execution_returns_404_or_conflict(client: TestClient) -> None:
    response = client.post(
        "/api/v1/incidents",
        json={"service": "api", "error": "unknown", "log": "unknown"},
    )
    assert response.status_code == 200
    incident_id = client.get("/api/v1/incidents").json()[0]["id"]

    assert (
        client.get(f"/api/v1/incidents/{incident_id}/agent-execution").status_code
        == 404
    )
    feedback = client.post(
        f"/api/v1/incidents/{incident_id}/resolution/feedback",
        json={"reviewer": "demo-reviewer"},
    )
    assert feedback.status_code == 409
