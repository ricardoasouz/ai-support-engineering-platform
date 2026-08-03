"""Cached runtime dependencies for event publication."""

from functools import lru_cache

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.events.dispatcher import OutboxDispatcher, OutboxRetryService
from app.events.producer import KafkaEventProducer


@lru_cache
def get_event_producer() -> KafkaEventProducer:
    """Return one Kafka producer per application process."""
    return KafkaEventProducer(get_settings())


@lru_cache
def get_outbox_dispatcher() -> OutboxDispatcher:
    """Return the process-wide durable outbox dispatcher."""
    settings = get_settings()
    return OutboxDispatcher(
        get_session_factory(),
        get_event_producer(),
        batch_size=settings.kafka_outbox_batch_size,
    )


@lru_cache
def get_outbox_retry_service() -> OutboxRetryService:
    """Return the background outbox retry service."""
    settings = get_settings()
    return OutboxRetryService(
        get_outbox_dispatcher(),
        poll_interval=settings.kafka_outbox_poll_interval_seconds,
    )
