"""Semantic pgvector retrieval for incident-resolution grounding."""

import math
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.ai.providers.base import EmbeddingProvider
from app.db.models import (
    IncidentRecord,
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
    KnowledgeEmbeddingRecord,
)
from app.knowledge.models import RetrievedChunk


def build_incident_query(incident: IncidentRecord) -> str:
    """Build a bounded retrieval query from persisted deterministic analysis."""
    actions = "; ".join(incident.recommended_actions)
    log_excerpt = incident.log[:2_000]
    return (
        "search_query: Support incident\n"
        f"Service: {incident.service}\n"
        f"Error: {incident.error}\n"
        f"Log excerpt: {log_excerpt}\n"
        f"Classification: {incident.classification}\n"
        f"Severity: {incident.resolved_severity}\n"
        f"Deterministic probable cause: {incident.probable_cause}\n"
        f"Deterministic actions: {actions}"
    )


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(float(a) * float(b) for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


class KnowledgeRetriever:
    """Retrieve nearest knowledge chunks using cosine similarity."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        embedding_provider: EmbeddingProvider,
        top_k: int,
    ) -> None:
        self.session_factory = session_factory
        self.embedding_provider = embedding_provider
        self.top_k = top_k

    def retrieve(self, incident: IncidentRecord) -> list[RetrievedChunk]:
        """Embed incident context and return the best grounded sources."""
        return self.retrieve_query(build_incident_query(incident))

    def retrieve_query(
        self,
        query_text: str,
        *,
        top_k: int | None = None,
        category: str | None = None,
    ) -> list[RetrievedChunk]:
        """Embed a bounded query and retrieve optional category-filtered chunks."""
        query = self.embedding_provider.embed_texts([query_text])[0]
        result_limit = min(top_k or self.top_k, self.top_k)
        with self.session_factory() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                return self._retrieve_postgresql(session, query, result_limit, category)
            return self._retrieve_portable(session, query, result_limit, category)

    def _retrieve_postgresql(
        self,
        session: Session,
        query: list[float],
        limit: int,
        category: str | None,
    ) -> list[RetrievedChunk]:
        distance = KnowledgeEmbeddingRecord.embedding.cosine_distance(query).label(
            "distance"
        )
        statement = (
            select(KnowledgeChunkRecord, KnowledgeDocumentRecord, distance)
            .join(
                KnowledgeEmbeddingRecord,
                KnowledgeEmbeddingRecord.chunk_id == KnowledgeChunkRecord.id,
            )
            .join(
                KnowledgeDocumentRecord,
                KnowledgeDocumentRecord.id == KnowledgeChunkRecord.document_id,
            )
            .order_by(distance, KnowledgeChunkRecord.id)
            .limit(limit)
        )
        if category is not None:
            statement = statement.where(KnowledgeDocumentRecord.category == category)
        rows = session.execute(statement).all()
        return [
            self._to_result(chunk, document, 1.0 - float(distance_value))
            for chunk, document, distance_value in rows
        ]

    def _retrieve_portable(
        self,
        session: Session,
        query: list[float],
        limit: int,
        category: str | None,
    ) -> list[RetrievedChunk]:
        statement = (
            select(
                KnowledgeChunkRecord,
                KnowledgeDocumentRecord,
                KnowledgeEmbeddingRecord,
            )
            .join(
                KnowledgeEmbeddingRecord,
                KnowledgeEmbeddingRecord.chunk_id == KnowledgeChunkRecord.id,
            )
            .join(
                KnowledgeDocumentRecord,
                KnowledgeDocumentRecord.id == KnowledgeChunkRecord.document_id,
            )
        )
        if category is not None:
            statement = statement.where(KnowledgeDocumentRecord.category == category)
        rows = session.execute(statement).all()
        scored = [
            (
                _cosine_similarity(embedding.embedding, query),
                chunk,
                document,
            )
            for chunk, document, embedding in rows
        ]
        scored.sort(key=lambda row: (-row[0], row[1].id))
        return [
            self._to_result(chunk, document, similarity)
            for similarity, chunk, document in scored[:limit]
        ]

    @staticmethod
    def _to_result(
        chunk: KnowledgeChunkRecord,
        document: KnowledgeDocumentRecord,
        similarity: float,
    ) -> RetrievedChunk:
        return RetrievedChunk(
            chunk_id=chunk.id,
            source_id=document.source_id,
            title=document.title,
            category=document.category,
            content=chunk.content,
            similarity=max(-1.0, min(1.0, similarity)),
        )
