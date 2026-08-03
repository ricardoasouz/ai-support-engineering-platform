"""Kafka consumer loop with manual offsets and safe poison-message handling."""

import logging
from collections.abc import Callable
from enum import Enum
from threading import Event
from typing import Any

from pydantic import ValidationError

from app.core.config import Settings
from app.events.models import IncidentCreatedEvent
from app.events.producer import EventPublishError, KafkaTopicManager
from app.workers.processor import (
    IncidentEventProcessor,
    ProcessingOutcome,
    RetryableProcessingError,
)

logger = logging.getLogger(__name__)


class MessageOutcome(str, Enum):
    """Consumer action resulting from one Kafka message."""

    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    MALFORMED = "malformed"
    RETRY = "retry"


class IncidentMessageHandler:
    """Deserialize an event and invoke deterministic processing safely."""

    def __init__(self, processor: IncidentEventProcessor) -> None:
        self.processor = processor

    def handle(self, value: bytes | None) -> MessageOutcome:
        """Classify malformed, retryable, duplicate, and successful messages."""
        if value is None:
            logger.warning("incident_event_malformed", extra={"error": "empty value"})
            return MessageOutcome.MALFORMED

        try:
            event = IncidentCreatedEvent.deserialize(value)
        except (ValidationError, ValueError, UnicodeDecodeError) as exc:
            logger.warning(
                "incident_event_malformed",
                extra={"error": str(exc)},
            )
            return MessageOutcome.MALFORMED

        try:
            outcome = self.processor.process(event)
        except RetryableProcessingError as exc:
            logger.warning(
                "incident_event_processing_deferred",
                extra={
                    "event_id": str(event.event_id),
                    "incident_id": event.incident_id,
                    "error": str(exc),
                },
            )
            return MessageOutcome.RETRY

        if outcome is ProcessingOutcome.DUPLICATE:
            return MessageOutcome.DUPLICATE
        return MessageOutcome.PROCESSED


class KafkaIncidentWorker:
    """Consume incident-created events with at-least-once delivery semantics."""

    def __init__(
        self,
        settings: Settings,
        handler: IncidentMessageHandler,
        *,
        consumer: Any | None = None,
        topic_manager: KafkaTopicManager | None = None,
        consumer_factory: Callable[[dict[str, object]], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.handler = handler
        consumer_config: dict[str, object] = {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "client.id": settings.kafka_worker_client_id,
            "group.id": settings.kafka_consumer_group,
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest",
            "logger": logging.getLogger("kafka.consumer"),
        }
        if consumer is None:
            if consumer_factory is None:
                from confluent_kafka import Consumer

                consumer_factory = Consumer
            consumer = consumer_factory(consumer_config)
        self.consumer = consumer
        self.topic_manager = topic_manager or KafkaTopicManager(settings)

    def wait_until_ready(self, stop: Event) -> bool:
        """Wait for Kafka and create the configured topic before consuming."""
        topic = self.settings.kafka_incident_created_topic
        while not stop.is_set():
            try:
                self.topic_manager.ensure_topic(topic)
                self.consumer.subscribe([topic])
                logger.info(
                    "incident_worker_ready",
                    extra={
                        "topic": topic,
                        "consumer_group": self.settings.kafka_consumer_group,
                    },
                )
                return True
            except EventPublishError as exc:
                logger.warning(
                    "incident_worker_waiting_for_kafka",
                    extra={"error": str(exc)},
                )
                stop.wait(self.settings.kafka_worker_retry_backoff_seconds)
        return False

    def run(self, stop: Event, on_ready: Any | None = None) -> None:
        """Poll until shutdown, committing only handled messages."""
        from confluent_kafka import KafkaException

        if not self.wait_until_ready(stop):
            self.consumer.close()
            return
        if on_ready is not None:
            on_ready()

        try:
            while not stop.is_set():
                message = self.consumer.poll(
                    self.settings.kafka_worker_poll_timeout_seconds
                )
                if message is None:
                    continue
                if message.error() is not None:
                    self._handle_consumer_error(message.error(), stop)
                    continue

                try:
                    outcome = self.handler.handle(message.value())
                except Exception:
                    logger.exception("incident_event_processing_failed")
                    self._seek_to_message(message)
                    stop.wait(self.settings.kafka_worker_retry_backoff_seconds)
                    continue

                if outcome is MessageOutcome.RETRY:
                    self._seek_to_message(message)
                    stop.wait(self.settings.kafka_worker_retry_backoff_seconds)
                    continue

                try:
                    self.consumer.commit(message=message, asynchronous=False)
                    logger.info(
                        "incident_event_offset_committed",
                        extra={
                            "topic": message.topic(),
                            "partition": message.partition(),
                            "offset": message.offset(),
                            "outcome": outcome.value,
                        },
                    )
                except KafkaException as exc:
                    logger.warning(
                        "incident_event_offset_commit_failed",
                        extra={"error": str(exc)},
                    )
                    self._seek_to_message(message)
                    stop.wait(self.settings.kafka_worker_retry_backoff_seconds)
        finally:
            self.consumer.close()
            logger.info("incident_worker_stopped")

    def _handle_consumer_error(self, error: Any, stop: Event) -> None:
        from confluent_kafka import KafkaError

        if error.code() == KafkaError._PARTITION_EOF:
            return
        log_method = logger.warning if error.retriable() else logger.error
        log_method("incident_worker_kafka_error", extra={"error": str(error)})
        stop.wait(self.settings.kafka_worker_retry_backoff_seconds)

    def _seek_to_message(self, message: Any) -> None:
        from confluent_kafka import TopicPartition

        self.consumer.seek(
            TopicPartition(
                message.topic(),
                message.partition(),
                message.offset(),
            )
        )
