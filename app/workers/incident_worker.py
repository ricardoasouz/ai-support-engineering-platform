"""Executable entry point for the incident Kafka worker."""

import logging
import signal
from pathlib import Path
from threading import Event

from app.agent.workflow import AgentLimits, ControlledAgentWorkflow
from app.ai.providers.factory import create_embedding_provider, create_llm_provider
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import dispose_engine, get_session_factory
from app.knowledge.retrieval import KnowledgeRetriever
from app.observability import configure_observability, shutdown_observability
from app.workers.processor import IncidentEventProcessor
from app.workers.runner import IncidentMessageHandler, KafkaIncidentWorker

logger = logging.getLogger(__name__)
WORKER_READY_FILE = Path("/tmp/incident-worker-ready")


def main() -> None:
    """Configure the worker, handle shutdown signals, and consume events."""
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_observability(settings)
    stop = Event()

    def request_shutdown(signum: int, _frame: object) -> None:
        logger.info("incident_worker_shutdown_requested", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    session_factory = get_session_factory()
    embedding_provider = create_embedding_provider(settings)
    llm_provider = create_llm_provider(settings)
    workflow = ControlledAgentWorkflow(
        session_factory,
        KnowledgeRetriever(
            session_factory,
            embedding_provider,
            settings.knowledge_retrieval_top_k,
        ),
        llm_provider,
        AgentLimits(
            max_steps=settings.agent_max_steps,
            max_tool_calls=settings.agent_max_tool_calls,
            max_repeated_tool_calls=settings.agent_max_repeated_tool_calls,
            max_retrieval_chunks=settings.agent_max_retrieval_chunks,
            max_duration_seconds=settings.agent_max_duration_seconds,
            tool_timeout_seconds=settings.agent_tool_timeout_seconds,
            model_retries=settings.agent_model_retries,
            repair_attempts=settings.agent_repair_attempts,
        ),
        planner_prompt_version=settings.agent_planner_prompt_version,
        resolver_prompt_version=settings.agent_resolver_prompt_version,
    )
    processor = IncidentEventProcessor(
        session_factory,
        settings.kafka_consumer_group,
        workflow,
    )
    worker = KafkaIncidentWorker(
        settings,
        IncidentMessageHandler(processor),
    )

    def mark_ready() -> None:
        WORKER_READY_FILE.touch()

    logger.info("incident_worker_starting")
    try:
        worker.run(stop, on_ready=mark_ready)
    finally:
        WORKER_READY_FILE.unlink(missing_ok=True)
        llm_provider.close()
        embedding_provider.close()
        shutdown_observability()
        dispose_engine()


if __name__ == "__main__":
    main()
