"""Kafka consumer loop with manual offsets and safe poison-message handling."""

import logging
import time
from collections.abc import Callable
from enum import Enum
from threading import Event
from typing import Any

from opentelemetry.trace import SpanKind
from pydantic import ValidationError

from app.core.config import Settings
from app.events.models import IncidentCreatedEvent
from app.events.producer import EventPublishError, KafkaTopicManager
from app.observability.context import extract_kafka_context, set_current_span_attributes
from app.observability.metrics import get_metrics
from app.observability.tracing import mark_span_error, start_span
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
    FAILED = "failed"


class IncidentMessageHandler:
    """Deserialize an event and invoke deterministic processing safely."""

    def __init__(self, processor: IncidentEventProcessor) -> None:
        self.processor = processor

    def handle(self, value: bytes | None) -> MessageOutcome:
        """Classify malformed, retryable, duplicate, and successful messages."""
        if value is None:
            get_metrics().count("kafka_malformed")
            logger.warning("incident_event_malformed", extra={"error": "empty value"})
            return MessageOutcome.MALFORMED

        with start_span("incident event validation") as validation_span:
            try:
                event = IncidentCreatedEvent.deserialize(value)
            except (ValidationError, ValueError, UnicodeDecodeError) as exc:
                mark_span_error(validation_span, exc)
                get_metrics().count("kafka_malformed")
                logger.warning(
                    "incident_event_malformed",
                    extra={"error": str(exc)},
                )
                return MessageOutcome.MALFORMED

        set_current_span_attributes(
            {
                "event.id": str(event.event_id),
                "event.type": event.event_type,
                "incident.id": event.incident_id,
                "incident.classification": event.classification.value,
                "incident.severity": event.severity.value,
            }
        )

        try:
            outcome = self.processor.process(event)
        except RetryableProcessingError as exc:
            get_metrics().count(
                "kafka_processing_failures",
                attributes={"event_type": event.event_type, "retryable": True},
            )
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
            get_metrics().count(
                "kafka_duplicates", attributes={"event_type": event.event_type}
            )
            return MessageOutcome.DUPLICATE
        if outcome is ProcessingOutcome.FAILED:
            get_metrics().count(
                "kafka_processing_failures",
                attributes={"event_type": event.event_type, "retryable": False},
            )
            return MessageOutcome.FAILED
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
            "max.poll.interval.ms": settings.kafka_worker_max_poll_interval_ms,
            "session.timeout.ms": settings.kafka_consumer_session_timeout_ms,
            "heartbeat.interval.ms": settings.kafka_consumer_heartbeat_interval_ms,
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

                headers_method = getattr(message, "headers", None)
                headers = headers_method() if callable(headers_method) else None
                parent = extract_kafka_context(headers)
                topic = message.topic()
                event_type = "incident.created"
                started = time.perf_counter()
                with start_span(
                    f"kafka process {topic}",
                    kind=SpanKind.CONSUMER,
                    context=parent,
                    attributes={
                        "messaging.system": "kafka",
                        "messaging.destination.name": topic,
                        "messaging.operation.type": "process",
                    },
                ) as processing_span:
                    get_metrics().count(
                        "kafka_events_consumed",
                        attributes={"topic": topic, "event_type": event_type},
                    )
                    try:
                        outcome = self.handler.handle(message.value())
                    except Exception as exc:
                        mark_span_error(processing_span, exc)
                        get_metrics().count(
                            "kafka_processing_failures",
                            attributes={"event_type": event_type, "retryable": True},
                        )
                        logger.exception("incident_event_processing_failed")
                        self._seek_to_message(message)
                        stop.wait(self.settings.kafka_worker_retry_backoff_seconds)
                        continue

                    get_metrics().observe(
                        "kafka_processing_duration_seconds",
                        time.perf_counter() - started,
                        {"event_type": event_type, "outcome": outcome.value},
                    )
                    if outcome is MessageOutcome.RETRY:
                        self._seek_to_message(message)
                        stop.wait(self.settings.kafka_worker_retry_backoff_seconds)
                        continue

                    try:
                        with start_span("kafka offset commit"):
                            self.consumer.commit(message=message, asynchronous=False)
                        get_metrics().count(
                            "kafka_offset_commit",
                            attributes={"topic": topic, "outcome": "success"},
                        )
                        get_metrics().count(
                            "kafka_events_processed",
                            attributes={
                                "event_type": event_type,
                                "outcome": outcome.value,
                            },
                        )
                        logger.info(
                            "incident_event_offset_committed",
                            extra={
                                "topic": topic,
                                "partition": message.partition(),
                                "offset": message.offset(),
                                "outcome": outcome.value,
                            },
                        )
                    except KafkaException as exc:
                        mark_span_error(processing_span, exc)
                        get_metrics().count(
                            "kafka_offset_commit",
                            attributes={"topic": topic, "outcome": "failure"},
                        )
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
