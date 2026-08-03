"""Persistence access for agent audit state and human review metadata."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.models import AgentStatus, FeedbackRequest
from app.db.models import (
    AgentExecutionRecord,
    AgentStepRecord,
    AIResolutionRecord,
    ResolutionFeedbackRecord,
)


class AgentRepository:
    """Read and update auditable agent state without invoking model inference."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_execution(self, incident_id: int) -> AgentExecutionRecord | None:
        return self.session.scalar(
            select(AgentExecutionRecord).where(
                AgentExecutionRecord.incident_id == incident_id
            )
        )

    def list_steps(self, execution_id: str) -> list[AgentStepRecord]:
        return list(
            self.session.scalars(
                select(AgentStepRecord)
                .where(AgentStepRecord.execution_id == execution_id)
                .order_by(AgentStepRecord.step_number)
            )
        )

    def add_feedback(
        self,
        incident_id: int,
        feedback: FeedbackRequest,
        *,
        outcome: str = "feedback",
    ) -> ResolutionFeedbackRecord | None:
        """Store feedback and optionally transition the execution review state."""
        resolution = self.session.scalar(
            select(AIResolutionRecord).where(
                AIResolutionRecord.incident_id == incident_id
            )
        )
        execution = self.get_execution(incident_id)
        if resolution is None or execution is None:
            return None
        accepted = outcome == "approved"
        record = ResolutionFeedbackRecord(
            incident_id=incident_id,
            resolution_id=resolution.id,
            rating=feedback.rating,
            outcome=outcome,
            accepted=accepted,
            edited=False,
            comment=feedback.comment,
            reviewer=feedback.reviewer,
            edited_resolution=None,
        )
        self.session.add(record)
        if outcome in {"approved", "rejected"}:
            execution.status = (
                AgentStatus.APPROVED if accepted else AgentStatus.REJECTED
            )
            execution.updated_at = datetime.now(UTC)
        self.session.commit()
        self.session.refresh(record)
        return record
