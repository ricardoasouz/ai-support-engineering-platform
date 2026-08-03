"""Executable entry point for the incident Kafka worker."""

import logging
import signal
from pathlib import Path
from threading import Event

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_session_factory
from app.workers.processor import IncidentEventProcessor
from app.workers.runner import IncidentMessageHandler, KafkaIncidentWorker

logger = logging.getLogger(__name__)
WORKER_READY_FILE = Path("/tmp/incident-worker-ready")


def main() -> None:
    """Configure the worker, handle shutdown signals, and consume events."""
    settings = get_settings()
    configure_logging(settings.log_level)
    stop = Event()

    def request_shutdown(signum: int, _frame: object) -> None:
        logger.info("incident_worker_shutdown_requested", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    processor = IncidentEventProcessor(
        get_session_factory(),
        settings.kafka_consumer_group,
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


if __name__ == "__main__":
    main()
