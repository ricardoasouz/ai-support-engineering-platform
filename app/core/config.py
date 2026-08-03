"""Environment-based application settings."""

from functools import lru_cache
from pathlib import Path
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
    kafka_worker_max_poll_interval_ms: int = Field(
        default=900_000,
        validation_alias="KAFKA_WORKER_MAX_POLL_INTERVAL_MS",
        ge=300_000,
    )
    kafka_worker_retry_backoff_seconds: float = Field(
        default=2.0,
        validation_alias="KAFKA_WORKER_RETRY_BACKOFF_SECONDS",
        gt=0,
    )
    llm_provider: Literal["ollama"] = Field(
        default="ollama", validation_alias="LLM_PROVIDER"
    )
    embedding_provider: Literal["ollama"] = Field(
        default="ollama", validation_alias="EMBEDDING_PROVIDER"
    )
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        validation_alias="OLLAMA_BASE_URL",
        min_length=1,
    )
    ollama_llm_model: str = Field(
        default="qwen2.5:1.5b-instruct",
        validation_alias="OLLAMA_LLM_MODEL",
        min_length=1,
    )
    ollama_embedding_model: str = Field(
        default="nomic-embed-text:v1.5",
        validation_alias="OLLAMA_EMBEDDING_MODEL",
        min_length=1,
    )
    ollama_request_timeout_seconds: float = Field(
        default=180.0,
        validation_alias="OLLAMA_REQUEST_TIMEOUT_SECONDS",
        gt=0,
    )
    embedding_dimensions: int = Field(
        default=768,
        validation_alias="EMBEDDING_DIMENSIONS",
        ge=1,
        le=2_000,
    )
    knowledge_base_path: Path = Field(
        default=Path("knowledge_base"),
        validation_alias="KNOWLEDGE_BASE_PATH",
    )
    knowledge_chunk_size: int = Field(
        default=900,
        validation_alias="KNOWLEDGE_CHUNK_SIZE",
        ge=200,
        le=8_000,
    )
    knowledge_retrieval_top_k: int = Field(
        default=4,
        validation_alias="KNOWLEDGE_RETRIEVAL_TOP_K",
        ge=1,
        le=20,
    )
    ai_max_attempts: int = Field(
        default=5,
        validation_alias="AI_MAX_ATTEMPTS",
        ge=1,
        le=100,
    )
    ai_processing_stale_seconds: int = Field(
        default=900,
        validation_alias="AI_PROCESSING_STALE_SECONDS",
        ge=30,
    )
    agent_max_steps: int = Field(
        default=8,
        validation_alias="AGENT_MAX_STEPS",
        ge=2,
        le=20,
    )
    agent_max_tool_calls: int = Field(
        default=6,
        validation_alias="AGENT_MAX_TOOL_CALLS",
        ge=1,
        le=15,
    )
    agent_max_repeated_tool_calls: int = Field(
        default=1,
        validation_alias="AGENT_MAX_REPEATED_TOOL_CALLS",
        ge=1,
        le=3,
    )
    agent_max_retrieval_chunks: int = Field(
        default=4,
        validation_alias="AGENT_MAX_RETRIEVAL_CHUNKS",
        ge=1,
        le=8,
    )
    agent_max_duration_seconds: float = Field(
        default=600.0,
        validation_alias="AGENT_MAX_DURATION_SECONDS",
        ge=10,
        le=1_800,
    )
    agent_tool_timeout_seconds: float = Field(
        default=180.0,
        validation_alias="AGENT_TOOL_TIMEOUT_SECONDS",
        ge=1,
        le=600,
    )
    agent_model_retries: int = Field(
        default=3,
        validation_alias="AGENT_MODEL_RETRIES",
        ge=1,
        le=10,
    )
    agent_repair_attempts: int = Field(
        default=2,
        validation_alias="AGENT_REPAIR_ATTEMPTS",
        ge=0,
        le=5,
    )
    agent_planner_prompt_version: Literal["v1"] = Field(
        default="v1", validation_alias="AGENT_PLANNER_PROMPT_VERSION"
    )
    agent_resolver_prompt_version: Literal["v1"] = Field(
        default="v1", validation_alias="AGENT_RESOLVER_PROMPT_VERSION"
    )


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings object per process."""
    return Settings()
