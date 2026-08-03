"""Export a sanitized evaluation dataset from explicit human feedback."""

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.db.models import AIResolutionRecord, IncidentRecord, ResolutionFeedbackRecord
from app.db.session import get_session_factory


def build_export_rows() -> list[dict[str, object]]:
    """Exclude raw logs, prompts, hidden reasoning, reviewer identity, and comments."""
    with get_session_factory()() as session:
        rows = session.execute(
            select(ResolutionFeedbackRecord, AIResolutionRecord, IncidentRecord)
            .join(
                AIResolutionRecord,
                AIResolutionRecord.id == ResolutionFeedbackRecord.resolution_id,
            )
            .join(
                IncidentRecord,
                IncidentRecord.id == ResolutionFeedbackRecord.incident_id,
            )
            .order_by(ResolutionFeedbackRecord.id)
        ).all()
    return [
        {
            "feedback_id": feedback.id,
            "incident_id": incident.id,
            "service": incident.service,
            "classification": incident.classification,
            "severity": incident.resolved_severity,
            "resolution": {
                "summary": resolution.summary,
                "root_cause": resolution.root_cause,
                "recommended_actions": resolution.recommended_actions,
                "confidence": resolution.confidence,
                "cited_sources": resolution.cited_sources,
                "limitations": resolution.limitations,
                "escalation_required": resolution.escalation_required,
            },
            "feedback": {
                "rating": feedback.rating,
                "outcome": feedback.outcome,
                "accepted": feedback.accepted,
                "edited": feedback.edited,
                "has_comment": feedback.comment is not None,
            },
        }
        for feedback, resolution, incident in rows
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    payload = {
        "dataset_version": "phase5-feedback-v1",
        "automatic_learning": False,
        "records": build_export_rows(),
    }
    arguments.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
