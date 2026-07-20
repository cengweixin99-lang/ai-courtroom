"""add Agent invocation leases and idempotency records

Revision ID: 20260720_0007
Revises: 20260718_0006
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0007"
down_revision: str | None = "20260718_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "court_sessions",
        sa.Column("active_agent_invocation_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "court_sessions",
        sa.Column("active_agent_lease_token", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "court_sessions",
        sa.Column("active_agent_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "agent_invocations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("lease_token", sa.String(length=36), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["session_id"], ["court_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "idempotency_key"),
    )
    op.create_index("ix_agent_invocations_session_id", "agent_invocations", ["session_id"])
    op.create_index("ix_agent_invocations_status", "agent_invocations", ["status"])


def downgrade() -> None:
    op.drop_index("ix_agent_invocations_status", table_name="agent_invocations")
    op.drop_index("ix_agent_invocations_session_id", table_name="agent_invocations")
    op.drop_table("agent_invocations")
    op.drop_column("court_sessions", "active_agent_lease_expires_at")
    op.drop_column("court_sessions", "active_agent_lease_token")
    op.drop_column("court_sessions", "active_agent_invocation_id")
