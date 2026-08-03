"""Kafka topic management and producer abstraction."""

import logging
from collections.abc import Callable
from typing import Any, Protocol

from opentelemetry.trace import SpanKind

from app.core.config import Settings
from app.events.models import IncidentCreatedEvent
from app.observability.context import inject_kafka_headers
from app.observability.metrics import get_metrics
from app.observability.tracing import mark_span_error, start_span


class EventPublishError(RuntimeError):
    """Raised when a domain event is not acknowledged by Kafka."""


class EventProducer(Protocol):
    """Port used by outbox dispatch without exposing a Kafka client."""

    def publish(self, event: IncidentCreatedEvent, topic: str) -> None:
        """Publish one event or raise EventPublishError."""

    def close(self) -> None:
        """Release producer resources."""


class KafkaTopicManager:
    """Create the configured topic idempotently through Kafka's admin API."""

    def __init__(
        self,
        settings: Settings,
        admin_client: Any | None = None,
    ) -> None:
        self.settings = settings
        if admin_client is None:
            from confluent_kafka.admin import AdminClient

            admin_client = AdminClient(
                {
                    "bootstrap.servers": settings.kafka_bootstrap_servers,
                    "client.id": f"{settings.kafka_producer_client_id}-admin",
                    "logger": logging.getLogger("kafka.admin"),
                }
            )
        self._admin = admin_client
        self._ready_topics: set[str] = set()

    def ensure_topic(self, topic: str) -> None:
        """Ensure the local-development topic exists with known partitioning."""
        if topic in self._ready_topics:
            return

        from confluent_kafka import KafkaError, KafkaException
        from confluent_kafka.admin import NewTopic

        futures = self._admin.create_topics(
            [
                NewTopic(
                    topic,
                    num_partitions=self.settings.kafka_topic_partitions,
                    replication_factor=1,
                )
            ],
            operation_timeout=5,
        )
        try:
            futures[topic].result(timeout=10)
        except KafkaException as exc:
            error = exc.args[0] if exc.args else None
            if not (
                isinstance(error, KafkaError)
                and error.code() == KafkaError.TOPIC_ALREADY_EXISTS
            ):
                raise EventPublishError(f"Kafka topic is unavailable: {exc}") from exc
        except Exception as exc:
            raise EventPublishError(f"Kafka topic is unavailable: {exc}") from exc

        self._ready_topics.add(topic)


class KafkaEventProducer:
    """Synchronous acknowledged Kafka publisher behind the producer port."""

    def __init__(
        self,
        settings: Settings,
        producer: Any | None = None,
        topic_manager: KafkaTopicManager | None = None,
        producer_factory: Callable[[dict[str, object]], Any] | None = None,
    ) -> None:
        self.settings = settings
        producer_config: dict[str, object] = {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "client.id": settings.kafka_producer_client_id,
            "acks": settings.kafka_producer_acks,
            "enable.idempotence": settings.kafka_producer_enable_idempotence,
            "delivery.timeout.ms": settings.kafka_producer_delivery_timeout_ms,
            "request.timeout.ms": settings.kafka_producer_request_timeout_ms,
            "retries": settings.kafka_producer_retries,
            "max.in.flight.requests.per.connection": 5,
            "logger": logging.getLogger("kafka.producer"),
        }
        if producer is None:
            if producer_factory is None:
                from confluent_kafka import Producer

                producer_factory = Producer
            producer = producer_factory(producer_config)
        self._producer = producer
        self._topic_manager = topic_manager or KafkaTopicManager(settings)

    def publish(self, event: IncidentCreatedEvent, topic: str) -> None:
        """Publish and wait for broker acknowledgement before returning."""
        attributes = {
            "messaging.system": "kafka",
            "messaging.destination.name": topic,
            "messaging.operation.type": "publish",
            "event.id": str(event.event_id),
            "event.type": event.event_type,
            "incident.id": event.incident_id,
        }
        metric_attributes = {"topic": topic, "event_type": event.event_type}
        with start_span(
            f"kafka publish {topic}",
            kind=SpanKind.PRODUCER,
            attributes=attributes,
        ) as span:
            try:
                self._publish(event, topic)
            except EventPublishError as exc:
                mark_span_error(span, exc)
                get_metrics().count(
                    "kafka_publish_failures", attributes=metric_attributes
                )
                raise
        get_metrics().count("kafka_events_published", attributes=metric_attributes)

    def _publish(self, event: IncidentCreatedEvent, topic: str) -> None:
        """Perform one acknowledged send beneath the producer trace span."""
        self._topic_manager.ensure_topic(topic)
        delivery_errors: list[str] = []

        def on_delivery(error: Any | None, _: Any) -> None:
            if error is not None:
                delivery_errors.append(str(error))

        try:
            self._producer.produce(
                topic=topic,
                key=str(event.incident_id).encode("utf-8"),
                value=event.serialize(),
                headers=inject_kafka_headers(),
                on_delivery=on_delivery,
            )
            remaining = self._producer.flush(
                self.settings.kafka_producer_delivery_timeout_ms / 1_000 + 1
            )
        except Exception as exc:
            raise EventPublishError(f"Kafka publish failed: {exc}") from exc

        if remaining:
            raise EventPublishError(
                f"Kafka publish timed out with {remaining} undelivered message(s)"
            )
        if delivery_errors:
            raise EventPublishError(f"Kafka delivery failed: {delivery_errors[0]}")

    def close(self) -> None:
        """Flush queued messages within the configured delivery timeout."""
        self._producer.flush(
            self.settings.kafka_producer_delivery_timeout_ms / 1_000 + 1
        )
