"""add independent court review turn evaluations

Revision ID: 20260721_0010
Revises: 20260721_0009
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0010"
down_revision: str | None = "20260721_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "court_review_evaluations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("review_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_cny", sa.Float(), nullable=False, server_default="0"),
        sa.Column("repair_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["review_id"], ["court_reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["court_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_id"),
    )
    op.create_index(
        op.f("ix_court_review_evaluations_review_id"),
        "court_review_evaluations",
        ["review_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_court_review_evaluations_session_id"),
        "court_review_evaluations",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_court_review_evaluations_session_id"), table_name="court_review_evaluations"
    )
    op.drop_index(
        op.f("ix_court_review_evaluations_review_id"), table_name="court_review_evaluations"
    )
    op.drop_table("court_review_evaluations")
