"""Add transactional outbox and processed-event idempotency tables.

Revision ID: 20260803_0002
Revises: 20260803_0001
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0002"
down_revision: str | Sequence[str] | None = "20260803_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create durable publication and consumer-idempotency storage."""
    op.create_table(
        "outbox_events",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("incident_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name=op.f("ck_outbox_events_attempts_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name="fk_outbox_events_incident_id_incidents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_outbox_events"),
    )
    op.create_index(
        "ix_outbox_events_incident_id",
        "outbox_events",
        ["incident_id"],
    )
    op.create_index(
        "ix_outbox_events_pending",
        "outbox_events",
        ["published_at", "next_attempt_at"],
    )

    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("incident_id", sa.Integer(), nullable=False),
        sa.Column("consumer_group", sa.String(length=255), nullable=False),
        sa.Column("processing_result", sa.JSON(), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name="fk_processed_events_incident_id_incidents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_processed_events"),
    )
    op.create_index(
        "ix_processed_events_incident_id",
        "processed_events",
        ["incident_id"],
    )


def downgrade() -> None:
    """Remove Phase 3 event processing state."""
    op.drop_index("ix_processed_events_incident_id", table_name="processed_events")
    op.drop_table("processed_events")
    op.drop_index("ix_outbox_events_pending", table_name="outbox_events")
    op.drop_index("ix_outbox_events_incident_id", table_name="outbox_events")
    op.drop_table("outbox_events")
