"""Add controlled agent execution, audit steps, and human feedback.

Revision ID: 20260803_0004
Revises: 20260803_0003
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0004"
down_revision: str | Sequence[str] | None = "20260803_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the Phase 5 audit and review schema."""
    op.add_column("ai_resolutions", sa.Column("evidence_summary", sa.Text()))
    op.add_column("ai_resolutions", sa.Column("tools_used", sa.JSON()))
    op.add_column("ai_resolutions", sa.Column("limitations", sa.JSON()))
    op.add_column("ai_resolutions", sa.Column("escalation_required", sa.Boolean()))
    op.add_column("ai_resolutions", sa.Column("human_review_recommended", sa.Boolean()))
    op.add_column("ai_resolutions", sa.Column("prompt_name", sa.String(length=80)))
    op.add_column("ai_resolutions", sa.Column("prompt_version", sa.String(length=30)))

    op.create_table(
        "agent_executions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("incident_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("planner_prompt_name", sa.String(length=80), nullable=False),
        sa.Column("planner_prompt_version", sa.String(length=30), nullable=False),
        sa.Column("resolver_prompt_name", sa.String(length=80), nullable=False),
        sa.Column("resolver_prompt_version", sa.String(length=30), nullable=False),
        sa.Column(
            "step_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "tool_call_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "model_call_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "retrieved_chunk_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("total_duration_ms", sa.Float(), nullable=True),
        sa.Column(
            "provider_duration_ms",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "retrieval_duration_ms",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "tool_duration_ms",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "approximate_input_chars",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "approximate_output_chars",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("provider_usage", sa.JSON(), nullable=True),
        sa.Column("error_type", sa.String(length=120), nullable=True),
        sa.Column("error_message_safe", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'waiting_for_tool', 'completed', "
            "'retryable', 'failed', 'cancelled', 'awaiting_review', 'approved', "
            "'rejected')",
            name="ck_agent_executions_agent_execution_status_values",
        ),
        sa.CheckConstraint(
            "step_count >= 0", name="ck_agent_executions_agent_step_count_non_negative"
        ),
        sa.CheckConstraint(
            "tool_call_count >= 0",
            name="ck_agent_executions_agent_tool_count_non_negative",
        ),
        sa.CheckConstraint(
            "model_call_count >= 0",
            name="ck_agent_executions_agent_model_count_non_negative",
        ),
        sa.CheckConstraint(
            "retry_count >= 0",
            name="ck_agent_executions_agent_retry_count_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name="fk_agent_executions_incident_id_incidents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_executions"),
        sa.UniqueConstraint("event_id", name="uq_agent_executions_event_id"),
        sa.UniqueConstraint("execution_id", name="uq_agent_executions_execution_id"),
        sa.UniqueConstraint("incident_id", name="uq_agent_executions_incident_id"),
    )
    op.create_index(
        "ix_agent_executions_created_at", "agent_executions", ["created_at"]
    )
    op.create_index("ix_agent_executions_status", "agent_executions", ["status"])

    op.create_table(
        "agent_steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(length=20), nullable=False),
        sa.Column("tool_name", sa.String(length=80), nullable=True),
        sa.Column("sanitized_arguments", sa.JSON(), nullable=True),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("evidence_references", sa.JSON(), nullable=False),
        sa.Column("reason_summary", sa.String(length=500), nullable=False),
        sa.Column("expected_evidence", sa.String(length=500), nullable=False),
        sa.Column("outcome", sa.String(length=80), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "duration_ms >= 0", name="ck_agent_steps_agent_step_duration_non_negative"
        ),
        sa.CheckConstraint(
            "step_number > 0", name="ck_agent_steps_agent_step_number_positive"
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["agent_executions.execution_id"],
            name="fk_agent_steps_execution_id_agent_executions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_steps"),
        sa.UniqueConstraint("execution_id", "step_number", name="uq_agent_step_order"),
    )
    op.create_index("ix_agent_steps_execution_id", "agent_steps", ["execution_id"])

    op.create_table(
        "resolution_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("incident_id", sa.Integer(), nullable=False),
        sa.Column("resolution_id", sa.Integer(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("edited", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("reviewer", sa.String(length=120), nullable=False),
        sa.Column("edited_resolution", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('feedback', 'approved', 'rejected')",
            name="ck_resolution_feedback_resolution_feedback_outcome_values",
        ),
        sa.CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name="ck_resolution_feedback_resolution_feedback_rating_range",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name="fk_resolution_feedback_incident_id_incidents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolution_id"],
            ["ai_resolutions.id"],
            name="fk_resolution_feedback_resolution_id_ai_resolutions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_resolution_feedback"),
    )
    op.create_index(
        "ix_resolution_feedback_created_at", "resolution_feedback", ["created_at"]
    )
    op.create_index(
        "ix_resolution_feedback_incident_id",
        "resolution_feedback",
        ["incident_id"],
    )


def downgrade() -> None:
    """Remove Phase 5 state while preserving Phase 4 data."""
    op.drop_index(
        "ix_resolution_feedback_incident_id", table_name="resolution_feedback"
    )
    op.drop_index("ix_resolution_feedback_created_at", table_name="resolution_feedback")
    op.drop_table("resolution_feedback")
    op.drop_index("ix_agent_steps_execution_id", table_name="agent_steps")
    op.drop_table("agent_steps")
    op.drop_index("ix_agent_executions_status", table_name="agent_executions")
    op.drop_index("ix_agent_executions_created_at", table_name="agent_executions")
    op.drop_table("agent_executions")
    op.drop_column("ai_resolutions", "prompt_version")
    op.drop_column("ai_resolutions", "prompt_name")
    op.drop_column("ai_resolutions", "human_review_recommended")
    op.drop_column("ai_resolutions", "escalation_required")
    op.drop_column("ai_resolutions", "limitations")
    op.drop_column("ai_resolutions", "tools_used")
    op.drop_column("ai_resolutions", "evidence_summary")
