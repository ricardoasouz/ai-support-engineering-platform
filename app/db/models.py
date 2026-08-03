"""SQLAlchemy mappings for persisted incidents."""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.incident import MAX_ERROR_LENGTH, MAX_SERVICE_LENGTH


class IncidentRecord(Base):
    """An input incident and the deterministic analysis produced for it."""

    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint(
            "requested_severity IS NULL OR "
            "requested_severity IN ('low', 'medium', 'high', 'critical')",
            name="requested_severity_values",
        ),
        CheckConstraint(
            "resolved_severity IN ('low', 'medium', 'high', 'critical')",
            name="resolved_severity_values",
        ),
        CheckConstraint(
            "classification IN ('authentication_error', "
            "'database_connection_error', 'timeout_error', 'unknown_error')",
            name="classification_values",
        ),
        Index("ix_incidents_service", "service"),
        Index("ix_incidents_resolved_severity", "resolved_severity"),
        Index("ix_incidents_classification", "classification"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service: Mapped[str] = mapped_column(String(MAX_SERVICE_LENGTH), nullable=False)
    error: Mapped[str] = mapped_column(String(MAX_ERROR_LENGTH), nullable=False)
    log: Mapped[str] = mapped_column(Text, nullable=False)
    requested_severity: Mapped[str | None] = mapped_column(String(8), nullable=True)
    resolved_severity: Mapped[str] = mapped_column(String(8), nullable=False)
    classification: Mapped[str] = mapped_column(String(50), nullable=False)
    probable_cause: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_actions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"IncidentRecord(id={self.id!r}, service={self.service!r})"


class OutboxEventRecord(Base):
    """A domain event awaiting or recording successful Kafka publication."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index(
            "ix_outbox_events_pending",
            "published_at",
            "next_attempt_at",
        ),
        Index("ix_outbox_events_incident_id", "incident_id"),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ProcessedEventRecord(Base):
    """Worker idempotency record and deterministic processing result."""

    __tablename__ = "processed_events"
    __table_args__ = (Index("ix_processed_events_incident_id", "incident_id"),)

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
    )
    consumer_group: Mapped[str] = mapped_column(String(255), nullable=False)
    processing_result: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class KnowledgeDocumentRecord(Base):
    """A versioned source document in the local support knowledge base."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (Index("ix_knowledge_documents_category", "category"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_metadata: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class KnowledgeChunkRecord(Base):
    """A deterministic, citation-addressable chunk of a source document."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunk_document_index"),
        Index("ix_knowledge_chunks_document_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeEmbeddingRecord(Base):
    """A pgvector embedding tied to one exact knowledge chunk version."""

    __tablename__ = "knowledge_embeddings"
    __table_args__ = (
        CheckConstraint("dimensions = 768", name="embedding_dimensions_768"),
        Index("ix_knowledge_embeddings_chunk_id", "chunk_id"),
        Index(
            "ix_knowledge_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ).ddl_if(dialect="postgresql"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_chunks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(768).with_variant(JSON(), "sqlite"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AIResolutionRecord(Base):
    """Durable state and grounded AI resolution for one incident."""

    __tablename__ = "ai_resolutions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'retryable', 'completed', 'failed')",
            name="ai_resolution_status_values",
        ),
        CheckConstraint("attempt_count >= 0", name="ai_attempts_non_negative"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ai_confidence_range",
        ),
        Index("ix_ai_resolutions_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_actions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    cited_sources: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSON, nullable=True
    )
    evidence_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools_used: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    limitations: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    escalation_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    human_review_recommended: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    retrieved_context: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSON, nullable=True
    )
    llm_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    embedding_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AgentExecutionRecord(Base):
    """Durable metadata for one bounded agent execution per incident."""

    __tablename__ = "agent_executions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'waiting_for_tool', 'completed', "
            "'retryable', 'failed', 'cancelled', 'awaiting_review', 'approved', "
            "'rejected')",
            name="agent_execution_status_values",
        ),
        CheckConstraint("step_count >= 0", name="agent_step_count_non_negative"),
        CheckConstraint("tool_call_count >= 0", name="agent_tool_count_non_negative"),
        CheckConstraint("model_call_count >= 0", name="agent_model_count_non_negative"),
        CheckConstraint("retry_count >= 0", name="agent_retry_count_non_negative"),
        Index("ix_agent_executions_status", "status"),
        Index("ix_agent_executions_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    event_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    planner_prompt_name: Mapped[str] = mapped_column(String(80), nullable=False)
    planner_prompt_version: Mapped[str] = mapped_column(String(30), nullable=False)
    resolver_prompt_name: Mapped[str] = mapped_column(String(80), nullable=False)
    resolver_prompt_version: Mapped[str] = mapped_column(String(30), nullable=False)
    step_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    tool_call_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    model_call_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    retrieved_chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    total_duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_duration_ms: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    retrieval_duration_ms: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    tool_duration_ms: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    approximate_input_chars: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    approximate_output_chars: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    provider_usage: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    error_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message_safe: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AgentStepRecord(Base):
    """Sanitized audit step with no hidden reasoning or complete prompts."""

    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint("execution_id", "step_number", name="uq_agent_step_order"),
        CheckConstraint("step_number > 0", name="agent_step_number_positive"),
        CheckConstraint("duration_ms >= 0", name="agent_step_duration_non_negative"),
        Index("ix_agent_steps_execution_id", "execution_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("agent_executions.execution_id", ondelete="CASCADE"),
        nullable=False,
    )
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    sanitized_arguments: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    result_summary: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    evidence_references: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False
    )
    reason_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    expected_evidence: Mapped[str] = mapped_column(String(500), nullable=False)
    outcome: Mapped[str] = mapped_column(String(80), nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ResolutionFeedbackRecord(Base):
    """Explicit demo human-review feedback; never an automatic training signal."""

    __tablename__ = "resolution_feedback"
    __table_args__ = (
        CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name="resolution_feedback_rating_range",
        ),
        CheckConstraint(
            "outcome IN ('feedback', 'approved', 'rejected')",
            name="resolution_feedback_outcome_values",
        ),
        Index("ix_resolution_feedback_incident_id", "incident_id"),
        Index("ix_resolution_feedback_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    resolution_id: Mapped[int] = mapped_column(
        ForeignKey("ai_resolutions.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    edited: Mapped[bool] = mapped_column(Boolean, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer: Mapped[str] = mapped_column(String(120), nullable=False)
    edited_resolution: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
