"""Kafka producer abstraction tests without a broker."""

from typing import Any

import pytest

from app.core.config import get_settings
from app.events.models import IncidentCreatedEvent
from app.events.producer import EventPublishError, KafkaEventProducer
from tests.test_events import build_event


class FakeTopicManager:
    """Record topic checks without Kafka."""

    def __init__(self) -> None:
        self.topics: list[str] = []

    def ensure_topic(self, topic: str) -> None:
        self.topics.append(topic)


class FakeKafkaProducer:
    """Invoke delivery callbacks deterministically."""

    def __init__(self, *, delivery_error: object | None = None, remaining: int = 0):
        self.delivery_error = delivery_error
        self.remaining = remaining
        self.messages: list[dict[str, Any]] = []

    def produce(self, **message: Any) -> None:
        self.messages.append(message)
        message["on_delivery"](self.delivery_error, object())

    def flush(self, _timeout: float) -> int:
        return self.remaining


def test_producer_serializes_key_and_value_and_waits_for_ack() -> None:
    fake = FakeKafkaProducer()
    topics = FakeTopicManager()
    producer = KafkaEventProducer(
        get_settings(),
        producer=fake,
        topic_manager=topics,
    )
    event = build_event()

    producer.publish(event, "incident.created")

    assert topics.topics == ["incident.created"]
    assert fake.messages[0]["key"] == b"42"
    assert IncidentCreatedEvent.deserialize(fake.messages[0]["value"]) == event


@pytest.mark.parametrize(
    "fake",
    [
        FakeKafkaProducer(delivery_error="broker unavailable"),
        FakeKafkaProducer(remaining=1),
    ],
)
def test_producer_raises_explicit_error_when_delivery_fails(
    fake: FakeKafkaProducer,
) -> None:
    producer = KafkaEventProducer(
        get_settings(),
        producer=fake,
        topic_manager=FakeTopicManager(),
    )

    with pytest.raises(EventPublishError):
        producer.publish(build_event(), "incident.created")


def test_producer_configuration_uses_reliable_settings() -> None:
    captured: dict[str, object] = {}
    fake = FakeKafkaProducer()

    def factory(config: dict[str, object]) -> FakeKafkaProducer:
        captured.update(config)
        return fake

    KafkaEventProducer(
        get_settings(),
        producer_factory=factory,
        topic_manager=FakeTopicManager(),
    )

    assert captured["acks"] == "all"
    assert captured["enable.idempotence"] is True
    assert captured["max.in.flight.requests.per.connection"] == 5
