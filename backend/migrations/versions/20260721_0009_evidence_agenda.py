"""add per-evidence response agenda

Revision ID: 20260721_0009
Revises: 20260720_0008
Create Date: 2026-07-21
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0009"
down_revision: str | None = "20260720_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_agenda_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=False),
        sa.Column("evidence_id", sa.String(length=64), nullable=False),
        sa.Column("submitted_by", sa.String(length=32), nullable=False),
        sa.Column("responding_role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("submission_event_sequence", sa.Integer(), nullable=True),
        sa.Column("response_event_sequence", sa.Integer(), nullable=True),
        sa.Column("response_action", sa.String(length=64), nullable=True),
        sa.Column("challenge_dimensions", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["session_id"], ["court_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "evidence_id"),
    )
    op.create_index(
        op.f("ix_evidence_agenda_items_session_id"),
        "evidence_agenda_items",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_evidence_agenda_items_status"),
        "evidence_agenda_items",
        ["status"],
        unique=False,
    )
    _backfill_existing_sessions()


def _backfill_existing_sessions() -> None:
    bind = op.get_bind()
    submissions = list(
        bind.execute(
            sa.text(
                "SELECT session_id, evidence_id, submitted_by, created_at "
                "FROM evidence_submissions ORDER BY id"
            )
        ).mappings()
    )
    agenda = sa.table(
        "evidence_agenda_items",
        sa.column("session_id", sa.String()),
        sa.column("phase", sa.String()),
        sa.column("evidence_id", sa.String()),
        sa.column("submitted_by", sa.String()),
        sa.column("responding_role", sa.String()),
        sa.column("status", sa.String()),
        sa.column("submission_event_sequence", sa.Integer()),
        sa.column("response_event_sequence", sa.Integer()),
        sa.column("response_action", sa.String()),
        sa.column("challenge_dimensions", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    events_by_session: dict[str, list[dict[str, Any]]] = {}

    for submission in submissions:
        session_id = str(submission["session_id"])
        events = events_by_session.get(session_id)
        if events is None:
            events = [
                dict(row)
                for row in bind.execute(
                    sa.text(
                        "SELECT sequence_number, phase, action, payload FROM session_events "
                        "WHERE session_id = :session_id ORDER BY sequence_number"
                    ),
                    {"session_id": session_id},
                ).mappings()
            ]
            events_by_session[session_id] = events

        evidence_id = str(submission["evidence_id"])
        submission_event = next(
            (
                item
                for item in events
                if item["action"] == "submit_evidence"
                and evidence_id in _payload(item["payload"]).get("evidence_ids", [])
            ),
            None,
        )
        response_event = next(
            (
                item
                for item in events
                if item["action"] == "challenge_evidence"
                and evidence_id in _payload(item["payload"]).get("evidence_ids", [])
            ),
            None,
        )
        submitted_by = str(submission["submitted_by"])
        phase = (
            str(submission_event["phase"])
            if submission_event is not None
            else _evidence_phase(submitted_by)
        )
        response_payload = _payload(response_event["payload"]) if response_event else {}
        created_at = submission["created_at"]

        # 历史数据没有独立议程表，只按不可变事件回放可确认的状态回填，不推断“无异议”。
        bind.execute(
            agenda.insert().values(
                session_id=session_id,
                phase=phase,
                evidence_id=evidence_id,
                submitted_by=submitted_by,
                responding_role="defense" if submitted_by == "prosecution" else "prosecution",
                status="challenged" if response_event is not None else "pending",
                submission_event_sequence=(
                    int(submission_event["sequence_number"])
                    if submission_event is not None
                    else None
                ),
                response_event_sequence=(
                    int(response_event["sequence_number"]) if response_event is not None else None
                ),
                response_action="challenge_evidence" if response_event is not None else None,
                challenge_dimensions=response_payload.get("challenge_dimensions", []),
                created_at=created_at,
                updated_at=created_at,
            )
        )


def _payload(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _evidence_phase(submitted_by: str) -> str:
    return (
        "PROSECUTION_EVIDENCE_AND_EXAMINATION"
        if submitted_by == "prosecution"
        else "DEFENSE_EVIDENCE_AND_EXAMINATION"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_evidence_agenda_items_status"), table_name="evidence_agenda_items")
    op.drop_index(op.f("ix_evidence_agenda_items_session_id"), table_name="evidence_agenda_items")
    op.drop_table("evidence_agenda_items")
