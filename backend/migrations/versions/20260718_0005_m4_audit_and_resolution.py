"""add M4 procedural resolutions and participant statement traces

Revision ID: 20260718_0005
Revises: 20260718_0004
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0005"
down_revision: str | None = "20260718_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "procedural_requests", sa.Column("resolution", sa.String(length=32), nullable=True)
    )
    op.add_column("procedural_requests", sa.Column("resolution_reason", sa.Text(), nullable=True))
    op.add_column(
        "procedural_requests",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "procedural_requests",
        sa.Column("resolution_event_sequence", sa.Integer(), nullable=True),
    )
    op.create_table(
        "participant_statement_traces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("participant_id", sa.String(length=64), nullable=False),
        sa.Column("actor_role", sa.String(length=32), nullable=False),
        sa.Column("event_sequence_number", sa.Integer(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("supported_statement_ids", sa.JSON(), nullable=False),
        sa.Column("related_fact_ids", sa.JSON(), nullable=False),
        sa.Column("consistency_status", sa.String(length=48), nullable=False),
        sa.Column("new_statement", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("refused_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["session_id"], ["court_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "event_sequence_number"),
    )
    op.create_index(
        "ix_participant_statement_traces_session_id",
        "participant_statement_traces",
        ["session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_participant_statement_traces_session_id",
        table_name="participant_statement_traces",
    )
    op.drop_table("participant_statement_traces")
    op.drop_column("procedural_requests", "resolution_event_sequence")
    op.drop_column("procedural_requests", "resolved_at")
    op.drop_column("procedural_requests", "resolution_reason")
    op.drop_column("procedural_requests", "resolution")
