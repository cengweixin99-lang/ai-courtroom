"""add new statement review and structured court review

Revision ID: 20260718_0006
Revises: 20260718_0005
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0006"
down_revision: str | None = "20260718_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "participant_statement_traces",
        sa.Column("review_status", sa.String(length=48), nullable=True),
    )
    op.add_column(
        "participant_statement_traces",
        sa.Column("review_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "participant_statement_traces",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "participant_statement_traces",
        sa.Column("review_event_sequence", sa.Integer(), nullable=True),
    )
    op.create_table(
        "court_reviews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("event_sequence_number", sa.Integer(), nullable=False),
        sa.Column("legal_search_trace_ids", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["session_id"], ["court_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
        sa.UniqueConstraint("session_id", "event_sequence_number"),
    )
    op.create_index("ix_court_reviews_session_id", "court_reviews", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_court_reviews_session_id", table_name="court_reviews")
    op.drop_table("court_reviews")
    op.drop_column("participant_statement_traces", "review_event_sequence")
    op.drop_column("participant_statement_traces", "reviewed_at")
    op.drop_column("participant_statement_traces", "review_reason")
    op.drop_column("participant_statement_traces", "review_status")
