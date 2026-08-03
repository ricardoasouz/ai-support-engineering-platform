"""Deterministic chunking, ingestion, and portable retrieval tests."""

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    IncidentRecord,
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
    KnowledgeEmbeddingRecord,
)
from app.knowledge.chunking import chunk_document
from app.knowledge.ingestion import KnowledgeIngestor
from app.knowledge.retrieval import KnowledgeRetriever


class FakeEmbeddings:
    provider_name = "fake"
    model_name = "deterministic-768"
    dimensions = 768

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            vector[0] = 1.0 if "JWT" in text or "authentication" in text else 0.1
            vector[1] = 1.0 if "PostgreSQL" in text else 0.1
            vectors.append(vector)
        return vectors


def test_chunking_is_deterministic_and_bounded() -> None:
    content = "# Heading\n\n" + ("first paragraph words " * 20) + "\n\nFinal paragraph"

    first = chunk_document(content, 200)
    second = chunk_document(content, 200)

    assert first == second
    assert all(len(chunk.content) <= 200 for chunk in first)
    assert [chunk.chunk_index for chunk in first] == list(range(len(first)))


def test_ingestion_is_idempotent(
    session_factory: sessionmaker[Session],
) -> None:
    ingestor = KnowledgeIngestor(session_factory, FakeEmbeddings(), 900)
    knowledge_path = Path(__file__).parents[1] / "knowledge_base"

    first = ingestor.ingest(knowledge_path)
    second = ingestor.ingest(knowledge_path)

    assert first.documents_updated == 5
    assert first.chunks_embedded > 5
    assert second.documents_updated == 0
    assert second.documents_unchanged == 5
    assert second.chunks_embedded == 0
    with session_factory() as session:
        document_count = session.scalar(
            select(func.count()).select_from(KnowledgeDocumentRecord)
        )
        chunk_count = session.scalar(
            select(func.count()).select_from(KnowledgeChunkRecord)
        )
        embedding_count = session.scalar(
            select(func.count()).select_from(KnowledgeEmbeddingRecord)
        )
    assert document_count == 5
    assert chunk_count == embedding_count == first.chunks_embedded


def test_portable_retrieval_ranks_semantic_match(
    session_factory: sessionmaker[Session],
) -> None:
    ingestor = KnowledgeIngestor(session_factory, FakeEmbeddings(), 900)
    ingestor.ingest(Path(__file__).parents[1] / "knowledge_base")
    with session_factory() as session:
        incident = IncidentRecord(
            service="identity-api",
            error="JWT expired",
            log="authentication token expired",
            requested_severity=None,
            resolved_severity="high",
            classification="authentication_error",
            probable_cause="Expired JWT",
            recommended_actions=["Refresh token"],
        )
        session.add(incident)
        session.commit()

    results = KnowledgeRetriever(session_factory, FakeEmbeddings(), 3).retrieve(
        incident
    )

    assert len(results) == 3
    assert results[0].source_id == "runbook-jwt-authentication"
    assert results[0].similarity >= results[1].similarity
