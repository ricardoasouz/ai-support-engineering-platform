"""CLI entry point for controlled knowledge ingestion."""

import logging

from app.ai.providers.factory import create_embedding_provider
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_session_factory
from app.knowledge.ingestion import KnowledgeIngestor

logger = logging.getLogger(__name__)


def main() -> None:
    """Embed repository runbooks and upsert their chunks."""
    settings = get_settings()
    configure_logging(settings.log_level)
    result = KnowledgeIngestor(
        get_session_factory(),
        create_embedding_provider(settings),
        settings.knowledge_chunk_size,
    ).ingest(settings.knowledge_base_path)
    logger.info(
        "knowledge_ingestion_completed",
        extra={
            "documents_updated": result.documents_updated,
            "documents_unchanged": result.documents_unchanged,
            "chunks_embedded": result.chunks_embedded,
        },
    )


if __name__ == "__main__":
    main()
