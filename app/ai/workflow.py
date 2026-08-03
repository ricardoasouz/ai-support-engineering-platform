"""Durable, retryable grounded-resolution workflow for the Kafka worker."""

import logging
from datetime import UTC, datetime, timedelta
from enum import Enum

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.ai.models import AIResolutionStatus, GeneratedResolution
from app.ai.prompting import (
    CitationValidationError,
    build_resolution_prompt,
    validate_citations,
)
from app.ai.providers.base import (
    LLMProvider,
    ProviderError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from app.db.models import AIResolutionRecord, IncidentRecord
from app.events.models import IncidentCreatedEvent
from app.knowledge.models import RetrievedChunk
from app.knowledge.retrieval import KnowledgeRetriever

logger = logging.getLogger(__name__)


class ResolutionWorkflowOutcome(str, Enum):
    """Terminal outcome visible to the event processor."""

    COMPLETED = "completed"
    FAILED = "failed"


class RetryableResolutionError(RuntimeError):
    """The resolution was durably deferred and should be delivered again."""


class RetrievalError(RuntimeError):
    """No usable grounding context was available."""


class IncidentResolutionWorkflow:
    """Coordinate retrieval, structured generation, and durable lifecycle state."""

    phase = 4

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        retriever: KnowledgeRetriever,
        llm_provider: LLMProvider,
        max_attempts: int,
        stale_seconds: int,
    ) -> None:
        self.session_factory = session_factory
        self.retriever = retriever
        self.llm_provider = llm_provider
        self.max_attempts = max_attempts
        self.stale_after = timedelta(seconds=stale_seconds)

    def resolve(self, event: IncidentCreatedEvent) -> ResolutionWorkflowOutcome:
        """Resolve an incident or persist enough state for a safe retry."""
        incident, attempt = self._claim(event)
        if attempt == 0:
            with self.session_factory() as session:
                record = session.scalar(
                    select(AIResolutionRecord).where(
                        AIResolutionRecord.incident_id == event.incident_id
                    )
                )
                if record is not None and record.status == AIResolutionStatus.COMPLETED:
                    return ResolutionWorkflowOutcome.COMPLETED
                return ResolutionWorkflowOutcome.FAILED

        context: list[RetrievedChunk] = []
        try:
            context = self.retriever.retrieve(incident)
            if not context:
                raise RetrievalError(
                    "No knowledge embeddings are available; run knowledge ingestion"
                )
            prompt = build_resolution_prompt(incident, context)
            resolution = self.llm_provider.generate_structured(
                prompt, GeneratedResolution
            )
            validate_citations(resolution, context)
        except (
            ProviderUnavailableError,
            RetrievalError,
            SQLAlchemyError,
        ) as exc:
            return self._record_failure(
                event, attempt, exc, context=context, can_be_terminal=False
            )
        except (ProviderResponseError, ProviderError, CitationValidationError) as exc:
            return self._record_failure(
                event, attempt, exc, context=context, can_be_terminal=True
            )

        now = datetime.now(UTC)
        with self.session_factory() as session:
            record = session.scalar(
                select(AIResolutionRecord).where(
                    AIResolutionRecord.incident_id == event.incident_id
                )
            )
            if record is None:
                raise RetryableResolutionError("Resolution state disappeared")
            record.status = AIResolutionStatus.COMPLETED
            record.summary = resolution.summary
            record.root_cause = resolution.root_cause
            record.recommended_actions = resolution.recommended_actions
            record.confidence = resolution.confidence
            record.cited_sources = [
                citation.model_dump(mode="json")
                for citation in resolution.cited_sources
            ]
            record.retrieved_context = [
                item.model_dump(mode="json") for item in context
            ]
            record.error_message = None
            record.completed_at = now
            record.updated_at = now
            session.commit()
        logger.info(
            "incident_ai_resolution_completed",
            extra={
                "event_id": str(event.event_id),
                "incident_id": event.incident_id,
                "attempt": attempt,
                "sources": len(resolution.cited_sources),
            },
        )
        return ResolutionWorkflowOutcome.COMPLETED

    def _claim(self, event: IncidentCreatedEvent) -> tuple[IncidentRecord, int]:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            incident = session.get(IncidentRecord, event.incident_id)
            if incident is None:
                raise RetryableResolutionError(
                    f"Incident {event.incident_id} is not visible in PostgreSQL"
                )
            record = session.scalar(
                select(AIResolutionRecord)
                .where(AIResolutionRecord.incident_id == event.incident_id)
                .with_for_update()
            )
            if record is not None and record.status in {
                AIResolutionStatus.COMPLETED,
                AIResolutionStatus.FAILED,
            }:
                return incident, 0
            if record is not None and record.status == AIResolutionStatus.PROCESSING:
                started = record.processing_started_at
                if started is not None and started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
                is_other_event = record.event_id != str(event.event_id)
                if (
                    is_other_event
                    and started is not None
                    and now - started < self.stale_after
                ):
                    raise RetryableResolutionError(
                        "Another event is currently resolving this incident"
                    )
            if record is None:
                record = AIResolutionRecord(
                    incident_id=event.incident_id,
                    event_id=str(event.event_id),
                    status=AIResolutionStatus.PROCESSING,
                    attempt_count=1,
                    llm_provider=self.llm_provider.provider_name,
                    llm_model=self.llm_provider.model_name,
                    embedding_provider=self.retriever.embedding_provider.provider_name,
                    embedding_model=self.retriever.embedding_provider.model_name,
                    processing_started_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                record.event_id = str(event.event_id)
                record.status = AIResolutionStatus.PROCESSING
                record.attempt_count += 1
                record.processing_started_at = now
                record.updated_at = now
                record.error_message = None
            session.commit()
            attempt = record.attempt_count
            session.expunge(incident)
            return incident, attempt

    def _record_failure(
        self,
        event: IncidentCreatedEvent,
        attempt: int,
        error: Exception,
        *,
        context: list[RetrievedChunk],
        can_be_terminal: bool,
    ) -> ResolutionWorkflowOutcome:
        terminal = can_be_terminal and attempt >= self.max_attempts
        next_status = (
            AIResolutionStatus.FAILED if terminal else AIResolutionStatus.RETRYABLE
        )
        now = datetime.now(UTC)
        with self.session_factory() as session:
            record = session.scalar(
                select(AIResolutionRecord).where(
                    AIResolutionRecord.incident_id == event.incident_id
                )
            )
            if record is None:
                raise RetryableResolutionError(
                    "Resolution state disappeared"
                ) from error
            record.status = next_status
            record.error_message = str(error)[:4_000]
            if context:
                record.retrieved_context = [
                    item.model_dump(mode="json") for item in context
                ]
            record.updated_at = now
            session.commit()
        logger.warning(
            "incident_ai_resolution_failed",
            extra={
                "event_id": str(event.event_id),
                "incident_id": event.incident_id,
                "attempt": attempt,
                "status": next_status.value,
                "error": str(error),
            },
        )
        if terminal:
            return ResolutionWorkflowOutcome.FAILED
        raise RetryableResolutionError(str(error)) from error
