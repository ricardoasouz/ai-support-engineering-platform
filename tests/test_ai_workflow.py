"""Grounded worker workflow reliability and idempotency tests."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai.models import GeneratedResolution, SourceCitation
from app.ai.providers.base import ProviderUnavailableError
from app.ai.workflow import IncidentResolutionWorkflow
from app.db.models import AIResolutionRecord, IncidentRecord, ProcessedEventRecord
from app.events.models import IncidentCreatedEvent
from app.knowledge.models import RetrievedChunk
from app.models.incident import IncidentClassification, Severity
from app.workers.processor import (
    IncidentEventProcessor,
    ProcessingOutcome,
    RetryableProcessingError,
)


class FakeEmbeddingProvider:
    provider_name = "fake"
    model_name = "fake-768"
    dimensions = 768

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 767 for _ in texts]


class FakeRetriever:
    embedding_provider = FakeEmbeddingProvider()

    def retrieve(self, _incident: IncidentRecord) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                chunk_id=7,
                source_id="runbook-jwt-authentication",
                title="JWT and Authentication Failures",
                category="authentication",
                content="Expired tokens must be refreshed.",
                similarity=0.95,
            )
        ]


class EmptyRetriever:
    embedding_provider = FakeEmbeddingProvider()

    def retrieve(self, _incident: IncidentRecord) -> list[RetrievedChunk]:
        return []


class MutableLLM:
    provider_name = "fake"
    model_name = "structured-fake"

    def __init__(self, *, unavailable: bool = False, bad_citation: bool = False):
        self.unavailable = unavailable
        self.bad_citation = bad_citation
        self.calls = 0

    def generate_structured(
        self, prompt: str, response_model: type[GeneratedResolution]
    ) -> GeneratedResolution:
        self.calls += 1
        assert "RETRIEVED RUNBOOK EVIDENCE" in prompt
        if self.unavailable:
            raise ProviderUnavailableError("Ollama offline")
        result = {
            "summary": "The identity request used an expired JWT.",
            "root_cause": "The token expiry time has passed.",
            "recommended_actions": ["Refresh the token and retry."],
            "confidence": 0.91,
            "cited_sources": [
                {
                    "source_id": "invented"
                    if self.bad_citation
                    else ("runbook-jwt-authentication"),
                    "chunk_id": 7,
                }
            ],
        }
        return response_model.model_validate(result)


def persist_incident(session_factory: sessionmaker[Session]) -> IncidentRecord:
    with session_factory() as session:
        incident = IncidentRecord(
            service="identity-api",
            error="JWT expired",
            log="Bearer token has expired",
            requested_severity=None,
            resolved_severity="high",
            classification="authentication_error",
            probable_cause="Expired token",
            recommended_actions=["Refresh the token"],
        )
        session.add(incident)
        session.commit()
        return incident


def event_for(incident_id: int) -> IncidentCreatedEvent:
    return IncidentCreatedEvent.create(
        incident_id=incident_id,
        service="identity-api",
        classification=IncidentClassification.AUTHENTICATION_ERROR,
        severity=Severity.HIGH,
    )


def workflow_for(
    session_factory: sessionmaker[Session],
    llm: MutableLLM,
    *,
    max_attempts: int = 3,
) -> IncidentResolutionWorkflow:
    return IncidentResolutionWorkflow(
        session_factory,
        FakeRetriever(),  # type: ignore[arg-type]
        llm,
        max_attempts,
        30,
    )


def test_resolution_is_durable_before_event_is_marked_processed(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_incident(session_factory)
    llm = MutableLLM()
    processor = IncidentEventProcessor(
        session_factory,
        "incident-processing-v1",
        workflow_for(session_factory, llm),
    )
    event = event_for(incident.id)

    assert processor.process(event) is ProcessingOutcome.PROCESSED
    assert processor.process(event) is ProcessingOutcome.DUPLICATE

    with session_factory() as session:
        resolution = session.scalar(
            select(AIResolutionRecord).where(
                AIResolutionRecord.incident_id == incident.id
            )
        )
        processed = session.get(ProcessedEventRecord, str(event.event_id))
    assert resolution is not None
    assert resolution.status == "completed"
    assert resolution.summary == "The identity request used an expired JWT."
    assert resolution.cited_sources == [
        SourceCitation(source_id="runbook-jwt-authentication", chunk_id=7).model_dump()
    ]
    assert processed is not None
    assert processed.processing_result["status"] == "completed"
    assert llm.calls == 1


def test_provider_outage_retries_without_premature_idempotency_marker(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_incident(session_factory)
    llm = MutableLLM(unavailable=True)
    processor = IncidentEventProcessor(
        session_factory,
        "incident-processing-v1",
        workflow_for(session_factory, llm, max_attempts=1),
    )
    event = event_for(incident.id)

    try:
        processor.process(event)
    except RetryableProcessingError:
        pass
    else:
        raise AssertionError("Unavailable provider must request Kafka redelivery")

    with session_factory() as session:
        resolution = session.scalar(select(AIResolutionRecord))
        processed_count = session.scalar(
            select(func.count()).select_from(ProcessedEventRecord)
        )
    assert resolution is not None
    assert resolution.status == "retryable"
    assert resolution.attempt_count == 1
    assert processed_count == 0

    llm.unavailable = False
    assert processor.process(event) is ProcessingOutcome.PROCESSED
    with session_factory() as session:
        resolution = session.scalar(select(AIResolutionRecord))
    assert resolution is not None
    assert resolution.status == "completed"
    assert resolution.attempt_count == 2


def test_hallucinated_citation_is_retryable(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_incident(session_factory)
    processor = IncidentEventProcessor(
        session_factory,
        "incident-processing-v1",
        workflow_for(session_factory, MutableLLM(bad_citation=True)),
    )

    try:
        processor.process(event_for(incident.id))
    except RetryableProcessingError as exc:
        assert "unknown citations" in str(exc)
    else:
        raise AssertionError("Unknown citations must not be persisted as a resolution")


def test_missing_retrieval_context_remains_retryable(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_incident(session_factory)
    llm = MutableLLM()
    workflow = IncidentResolutionWorkflow(
        session_factory,
        EmptyRetriever(),  # type: ignore[arg-type]
        llm,
        1,
        30,
    )
    processor = IncidentEventProcessor(
        session_factory, "incident-processing-v1", workflow
    )

    try:
        processor.process(event_for(incident.id))
    except RetryableProcessingError as exc:
        assert "No knowledge embeddings" in str(exc)
    else:
        raise AssertionError("Missing retrieval context must request redelivery")
    assert llm.calls == 0
    with session_factory() as session:
        resolution = session.scalar(select(AIResolutionRecord))
        processed_count = session.scalar(
            select(func.count()).select_from(ProcessedEventRecord)
        )
    assert resolution is not None
    assert resolution.status == "retryable"
    assert processed_count == 0


def test_exhausted_attempts_persist_terminal_failure(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_incident(session_factory)
    processor = IncidentEventProcessor(
        session_factory,
        "incident-processing-v1",
        workflow_for(session_factory, MutableLLM(bad_citation=True), max_attempts=1),
    )
    event = event_for(incident.id)

    assert processor.process(event) is ProcessingOutcome.FAILED
    with session_factory() as session:
        resolution = session.scalar(select(AIResolutionRecord))
        processed = session.get(ProcessedEventRecord, str(event.event_id))
    assert resolution is not None
    assert resolution.status == "failed"
    assert processed is not None
    assert processed.processing_result["status"] == "failed"


def test_restart_after_resolution_commit_does_not_repeat_inference(
    session_factory: sessionmaker[Session],
) -> None:
    incident = persist_incident(session_factory)
    event = event_for(incident.id)
    with session_factory() as session:
        session.add(
            AIResolutionRecord(
                incident_id=incident.id,
                event_id=str(event.event_id),
                status="completed",
                attempt_count=1,
                summary="Already durable",
                root_cause="Expired token",
                recommended_actions=["Refresh token"],
                confidence=0.9,
                cited_sources=[
                    {
                        "source_id": "runbook-jwt-authentication",
                        "chunk_id": 7,
                    }
                ],
                retrieved_context=[],
            )
        )
        session.commit()

    llm = MutableLLM()
    processor = IncidentEventProcessor(
        session_factory,
        "incident-processing-v1",
        workflow_for(session_factory, llm),
    )

    assert processor.process(event) is ProcessingOutcome.PROCESSED
    assert llm.calls == 0
    with session_factory() as session:
        assert session.get(ProcessedEventRecord, str(event.event_id)) is not None
