"""Add durable W3C trace context to transactional outbox rows.

Revision ID: 20260803_0005
Revises: 20260803_0004
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0005"
down_revision: str | Sequence[str] | None = "20260803_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist only traceparent/tracestate for delayed outbox dispatch."""
    op.add_column("outbox_events", sa.Column("trace_context", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove durable propagation metadata without touching event payloads."""
    op.drop_column("outbox_events", "trace_context")
