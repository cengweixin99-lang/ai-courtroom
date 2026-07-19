"""add M4 structured procedural requests

Revision ID: 20260718_0004
Revises: 20260718_0003
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0004"
down_revision: str | None = "20260718_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "procedural_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("request_type", sa.String(length=48), nullable=False),
        sa.Column("raised_by", sa.String(length=32), nullable=False),
        sa.Column("event_sequence_number", sa.Integer(), nullable=False),
        sa.Column("target_event_sequence", sa.Integer(), nullable=True),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("challenge_dimensions", sa.JSON(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=48), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["session_id"], ["court_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "event_sequence_number"),
    )
    op.create_index("ix_procedural_requests_session_id", "procedural_requests", ["session_id"])
    op.create_index("ix_procedural_requests_status", "procedural_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_procedural_requests_status", table_name="procedural_requests")
    op.drop_index("ix_procedural_requests_session_id", table_name="procedural_requests")
    op.drop_table("procedural_requests")
