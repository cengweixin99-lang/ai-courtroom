"""add M3.4 legal search trace table

Revision ID: 20260718_0003
Revises: 20260716_0002
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0003"
down_revision: str | None = "20260716_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "legal_search_traces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_id", sa.Integer(), nullable=False),
        sa.Column("legal_profile_id", sa.String(length=128), nullable=False),
        sa.Column("query", sa.String(length=500), nullable=False),
        sa.Column("retrieval_mode", sa.String(length=32), nullable=False),
        sa.Column("embedding_version", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("hits", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["package_id"], ["case_packages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_legal_search_traces_package_id", "legal_search_traces", ["package_id"])
    op.create_index("ix_legal_search_traces_outcome", "legal_search_traces", ["outcome"])


def downgrade() -> None:
    op.drop_index("ix_legal_search_traces_outcome", table_name="legal_search_traces")
    op.drop_index("ix_legal_search_traces_package_id", table_name="legal_search_traces")
    op.drop_table("legal_search_traces")
