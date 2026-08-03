"""Environment-based application settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(validation_alias="DATABASE_URL", min_length=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        validation_alias="LOG_LEVEL",
    )
    kafka_enabled: bool = Field(default=True, validation_alias="KAFKA_ENABLED")
    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        validation_alias="KAFKA_BOOTSTRAP_SERVERS",
        min_length=1,
    )
    kafka_incident_created_topic: str = Field(
        default="incident.created",
        validation_alias="KAFKA_INCIDENT_CREATED_TOPIC",
        min_length=1,
    )
    kafka_topic_partitions: int = Field(
        default=3,
        validation_alias="KAFKA_TOPIC_PARTITIONS",
        ge=1,
    )
    kafka_consumer_group: str = Field(
        default="incident-processing-v1",
        validation_alias="KAFKA_CONSUMER_GROUP",
        min_length=1,
    )
    kafka_producer_client_id: str = Field(
        default="support-api",
        validation_alias="KAFKA_PRODUCER_CLIENT_ID",
        min_length=1,
    )
    kafka_producer_acks: Literal["all", "1", "0"] = Field(
        default="all",
        validation_alias="KAFKA_PRODUCER_ACKS",
    )
    kafka_producer_enable_idempotence: bool = Field(
        default=True,
        validation_alias="KAFKA_PRODUCER_ENABLE_IDEMPOTENCE",
    )
    kafka_producer_delivery_timeout_ms: int = Field(
        default=5_000,
        validation_alias="KAFKA_PRODUCER_DELIVERY_TIMEOUT_MS",
        ge=1_000,
    )
    kafka_producer_retries: int = Field(
        default=5,
        validation_alias="KAFKA_PRODUCER_RETRIES",
        ge=0,
    )
    kafka_outbox_poll_interval_seconds: float = Field(
        default=5.0,
        validation_alias="KAFKA_OUTBOX_POLL_INTERVAL_SECONDS",
        gt=0,
    )
    kafka_outbox_batch_size: int = Field(
        default=20,
        validation_alias="KAFKA_OUTBOX_BATCH_SIZE",
        ge=1,
        le=500,
    )
    kafka_worker_client_id: str = Field(
        default="incident-worker",
        validation_alias="KAFKA_WORKER_CLIENT_ID",
        min_length=1,
    )
    kafka_worker_poll_timeout_seconds: float = Field(
        default=1.0,
        validation_alias="KAFKA_WORKER_POLL_TIMEOUT_SECONDS",
        gt=0,
    )
    kafka_worker_retry_backoff_seconds: float = Field(
        default=2.0,
        validation_alias="KAFKA_WORKER_RETRY_BACKOFF_SECONDS",
        gt=0,
    )


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings object per process."""
    return Settings()
