"""Idempotent knowledge ingestion and embedding persistence."""

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai.providers.base import EmbeddingProvider
from app.db.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
    KnowledgeEmbeddingRecord,
)
from app.knowledge.chunking import chunk_document
from app.knowledge.loader import load_knowledge_sources
from app.knowledge.models import KnowledgeChunk

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionResult:
    """Counts from one controlled ingestion pass."""

    documents_updated: int = 0
    documents_unchanged: int = 0
    chunks_embedded: int = 0


class KnowledgeIngestor:
    """Upsert local sources and replace changed chunks atomically per document."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        embedding_provider: EmbeddingProvider,
        chunk_size: int,
    ) -> None:
        if embedding_provider.dimensions != 768:
            raise ValueError(
                "Phase 4 schema requires 768-dimensional embeddings; "
                f"provider declared {embedding_provider.dimensions}"
            )
        self.session_factory = session_factory
        self.embedding_provider = embedding_provider
        self.chunk_size = chunk_size

    def ingest(self, base_path: Path) -> IngestionResult:
        """Ingest every manifest source without duplicating unchanged content."""
        updated = unchanged = embedded = 0
        for source in load_knowledge_sources(base_path):
            chunks = chunk_document(source.content, self.chunk_size)
            with self.session_factory() as session:
                document = session.scalar(
                    select(KnowledgeDocumentRecord).where(
                        KnowledgeDocumentRecord.source_id == source.source_id
                    )
                )
                metadata_current = document is not None and (
                    document.title == source.title
                    and document.category == source.category
                    and document.version == source.version
                    and document.source_metadata == {"path": source.path}
                )
                if metadata_current and self._is_current(
                    session, document, source.content_hash, chunks
                ):
                    unchanged += 1
                    continue

            vectors = self.embedding_provider.embed_texts(
                [f"search_document: {chunk.content}" for chunk in chunks]
            )
            with self.session_factory() as session:
                document = session.scalar(
                    select(KnowledgeDocumentRecord).where(
                        KnowledgeDocumentRecord.source_id == source.source_id
                    )
                )
                if document is None:
                    document = KnowledgeDocumentRecord(
                        source_id=source.source_id,
                        title=source.title,
                        category=source.category,
                        version=source.version,
                        content_hash=source.content_hash,
                        source_metadata={"path": source.path},
                    )
                    session.add(document)
                    session.flush()
                else:
                    chunk_ids = list(
                        session.scalars(
                            select(KnowledgeChunkRecord.id).where(
                                KnowledgeChunkRecord.document_id == document.id
                            )
                        )
                    )
                    if chunk_ids:
                        session.execute(
                            delete(KnowledgeEmbeddingRecord).where(
                                KnowledgeEmbeddingRecord.chunk_id.in_(chunk_ids)
                            )
                        )
                    session.execute(
                        delete(KnowledgeChunkRecord).where(
                            KnowledgeChunkRecord.document_id == document.id
                        )
                    )
                    document.title = source.title
                    document.category = source.category
                    document.version = source.version
                    document.content_hash = source.content_hash
                    document.source_metadata = {"path": source.path}

                for chunk, vector in zip(chunks, vectors, strict=True):
                    chunk_record = KnowledgeChunkRecord(
                        document_id=document.id,
                        chunk_index=chunk.chunk_index,
                        content=chunk.content,
                        content_hash=chunk.content_hash,
                    )
                    session.add(chunk_record)
                    session.flush()
                    session.add(
                        KnowledgeEmbeddingRecord(
                            chunk_id=chunk_record.id,
                            provider=self.embedding_provider.provider_name,
                            model=self.embedding_provider.model_name,
                            dimensions=self.embedding_provider.dimensions,
                            content_hash=chunk.content_hash,
                            embedding=vector,
                        )
                    )
                session.commit()
            updated += 1
            embedded += len(chunks)
            logger.info(
                "knowledge_source_ingested",
                extra={"source_id": source.source_id, "chunks": len(chunks)},
            )
        return IngestionResult(updated, unchanged, embedded)

    def _is_current(
        self,
        session: Session,
        document: KnowledgeDocumentRecord,
        expected_source_hash: str,
        chunks: list[KnowledgeChunk],
    ) -> bool:
        if not chunks or document.content_hash != expected_source_hash:
            return False
        rows = session.execute(
            select(KnowledgeChunkRecord, KnowledgeEmbeddingRecord)
            .join(
                KnowledgeEmbeddingRecord,
                KnowledgeEmbeddingRecord.chunk_id == KnowledgeChunkRecord.id,
            )
            .where(KnowledgeChunkRecord.document_id == document.id)
            .order_by(KnowledgeChunkRecord.chunk_index)
        ).all()
        if len(rows) != len(chunks):
            return False
        return all(
            row_chunk.chunk_index == expected.chunk_index
            and row_chunk.content_hash == expected.content_hash
            and embedding.content_hash == expected.content_hash
            and embedding.provider == self.embedding_provider.provider_name
            and embedding.model == self.embedding_provider.model_name
            and embedding.dimensions == self.embedding_provider.dimensions
            for (row_chunk, embedding), expected in zip(rows, chunks, strict=True)
        )
