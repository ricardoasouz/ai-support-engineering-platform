"""Create the incidents table.

Revision ID: 20260803_0001
Revises:
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create persistent incident storage and filter indexes."""
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service", sa.String(length=100), nullable=False),
        sa.Column("error", sa.String(length=1000), nullable=False),
        sa.Column("log", sa.Text(), nullable=False),
        sa.Column("requested_severity", sa.String(length=8), nullable=True),
        sa.Column("resolved_severity", sa.String(length=8), nullable=False),
        sa.Column("classification", sa.String(length=50), nullable=False),
        sa.Column("probable_cause", sa.Text(), nullable=False),
        sa.Column("recommended_actions", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "classification IN ('authentication_error', "
            "'database_connection_error', 'timeout_error', 'unknown_error')",
            name="ck_incidents_classification_values",
        ),
        sa.CheckConstraint(
            "requested_severity IS NULL OR "
            "requested_severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_incidents_requested_severity_values",
        ),
        sa.CheckConstraint(
            "resolved_severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_incidents_resolved_severity_values",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_incidents"),
    )
    op.create_index("ix_incidents_classification", "incidents", ["classification"])
    op.create_index(
        "ix_incidents_resolved_severity",
        "incidents",
        ["resolved_severity"],
    )
    op.create_index("ix_incidents_service", "incidents", ["service"])


def downgrade() -> None:
    """Remove persistent incident storage."""
    op.drop_index("ix_incidents_service", table_name="incidents")
    op.drop_index("ix_incidents_resolved_severity", table_name="incidents")
    op.drop_index("ix_incidents_classification", table_name="incidents")
    op.drop_table("incidents")
