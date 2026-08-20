"""add session role claim index

Revision ID: 20260726_0013
Revises: 20260723_0012
Create Date: 2026-07-26
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20260726_0013"
down_revision: str | None = "20260723_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CLAIM_ACTIONS = ("make_statement", "submit_evidence", "challenge_evidence")
_ADVOCATE_ROLES = ("prosecution", "defense")


def upgrade() -> None:
    op.create_table(
        "session_role_claims",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("event_sequence_number", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=False),
        sa.Column("claim_type", sa.String(length=32), nullable=False),
        sa.Column("fact_ids", sa.JSON(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["session_id"], ["court_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_session_role_claims_session_id"),
        "session_role_claims",
        ["session_id"],
        unique=False,
    )
    _backfill_existing_claims()


def _backfill_existing_claims() -> None:
    """从历史庭审事件回放律师结构化主张，保证既有会话也能使用索引。"""
    bind = op.get_bind()
    claims_table = sa.table(
        "session_role_claims",
        sa.column("session_id", sa.String()),
        sa.column("event_sequence_number", sa.Integer()),
        sa.column("role", sa.String()),
        sa.column("phase", sa.String()),
        sa.column("claim_type", sa.String()),
        sa.column("fact_ids", sa.JSON()),
        sa.column("text", sa.Text()),
    )
    events = list(
        bind.execute(
            sa.text(
                "SELECT session_id, sequence_number, phase, actor_role, action, payload "
                "FROM session_events ORDER BY session_id, sequence_number"
            )
        ).mappings()
    )
    for event in events:
        action = str(event["action"])
        role = str(event["actor_role"])
        if action not in _CLAIM_ACTIONS or role not in _ADVOCATE_ROLES:
            continue
        agent_output = _payload(event["payload"]).get("agent_output") or {}
        if not isinstance(agent_output, dict):
            continue
        for claim in agent_output.get("claims") or []:
            if not isinstance(claim, dict):
                continue
            text = str(claim.get("text") or "").strip()
            if not text:
                continue
            bind.execute(
                claims_table.insert().values(
                    session_id=str(event["session_id"]),
                    event_sequence_number=int(event["sequence_number"]),
                    role=role,
                    phase=str(event["phase"]),
                    claim_type=str(claim.get("claim_type") or "supported_fact"),
                    fact_ids=list(claim.get("fact_ids") or []),
                    text=text,
                )
            )


def _payload(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def downgrade() -> None:
    op.drop_index(op.f("ix_session_role_claims_session_id"), table_name="session_role_claims")
    op.drop_table("session_role_claims")
