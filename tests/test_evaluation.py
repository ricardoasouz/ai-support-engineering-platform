"""Golden evaluation and sanitized export behavior tests."""

from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    AIResolutionRecord,
    IncidentRecord,
    ResolutionFeedbackRecord,
)
from app.evaluation.evaluator import evaluate, load_golden_cases
from app.evaluation.export_feedback import build_export_rows
from app.evaluation.fake import golden_fake_candidate


def test_original_golden_dataset_covers_required_scenarios() -> None:
    cases = load_golden_cases()
    assert {case.case_id for case in cases} == {
        "jwt-expired",
        "jwt-invalid-issuer-audience",
        "postgres-connection-refused",
        "postgres-pool-exhausted",
        "gateway-timeout",
        "dns-network-failure",
        "container-unavailable",
        "insufficient-evidence",
    }


def test_fake_provider_golden_evaluation_is_deterministic() -> None:
    report = evaluate(
        golden_fake_candidate,
        provider="deterministic-fake",
        model="phase5-golden-v1",
    )
    assert len(report.cases) == 8
    assert report.aggregate["schema_valid"] == 1
    assert report.aggregate["within_budgets"] == 1
    assert report.aggregate["classification_correct"] == 1
    assert report.aggregate["no_fabricated_sources"] == 1


def test_feedback_export_excludes_raw_logs_prompts_comments_and_reviewer(
    session_factory: sessionmaker[Session], monkeypatch
) -> None:
    with session_factory() as session:
        incident = IncidentRecord(
            service="identity-api",
            error="JWT expired",
            log="raw-log-secret",
            requested_severity=None,
            resolved_severity="high",
            classification="authentication_error",
            probable_cause="Expired token",
            recommended_actions=["Refresh token"],
        )
        session.add(incident)
        session.flush()
        resolution = AIResolutionRecord(
            incident_id=incident.id,
            event_id=str(__import__("uuid").uuid4()),
            status="completed",
            attempt_count=1,
            summary="Expired JWT",
            root_cause="Expiry elapsed",
            recommended_actions=["Refresh token"],
            confidence=0.9,
            cited_sources=[],
            retrieved_context=[],
            prompt_name="resolver",
            prompt_version="v1",
        )
        session.add(resolution)
        session.flush()
        session.add(
            ResolutionFeedbackRecord(
                incident_id=incident.id,
                resolution_id=resolution.id,
                rating=4,
                outcome="feedback",
                accepted=False,
                edited=False,
                comment="comment-secret",
                reviewer="reviewer-secret",
            )
        )
        session.commit()

    monkeypatch.setattr(
        "app.evaluation.export_feedback.get_session_factory", lambda: session_factory
    )
    encoded = str(build_export_rows())
    assert "raw-log-secret" not in encoded
    assert "comment-secret" not in encoded
    assert "reviewer-secret" not in encoded
    assert "resolver" not in encoded
