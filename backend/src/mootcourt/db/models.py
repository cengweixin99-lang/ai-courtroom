from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mootcourt.db.base import Base


class CasePackageModel(Base):
    __tablename__ = "case_packages"
    __table_args__ = (UniqueConstraint("case_id", "package_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(64), index=True)
    package_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(48), index=True)
    title: Mapped[str] = mapped_column(String(255))
    jurisdiction: Mapped[str] = mapped_column(String(64))
    law_as_of_date: Mapped[date] = mapped_column(Date)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    case_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    legal_profile: Mapped[dict[str, Any]] = mapped_column(JSON)
    legal_issues: Mapped[dict[str, Any]] = mapped_column(JSON)
    procedure_profile: Mapped[dict[str, Any]] = mapped_column(JSON)
    review_manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    facts: Mapped[list[FactModel]] = relationship(cascade="all, delete-orphan")
    evidence: Mapped[list[EvidenceModel]] = relationship(cascade="all, delete-orphan")
    participants: Mapped[list[ParticipantModel]] = relationship(cascade="all, delete-orphan")
    role_materials: Mapped[list[RoleMaterialModel]] = relationship(cascade="all, delete-orphan")
    legal_sources: Mapped[list[LegalSourceModel]] = relationship(cascade="all, delete-orphan")
    sessions: Mapped[list[CourtSessionModel]] = relationship(
        back_populates="package",
        passive_deletes=True,
    )


class FactModel(Base):
    __tablename__ = "facts"
    __table_args__ = (UniqueConstraint("package_id", "fact_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("case_packages.id", ondelete="CASCADE"))
    fact_id: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class EvidenceModel(Base):
    __tablename__ = "evidence_items"
    __table_args__ = (UniqueConstraint("package_id", "evidence_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("case_packages.id", ondelete="CASCADE"))
    evidence_id: Mapped[str] = mapped_column(String(64))
    evidence_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    available_to: Mapped[list[str]] = mapped_column(JSON)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ParticipantModel(Base):
    __tablename__ = "participants"
    __table_args__ = (UniqueConstraint("package_id", "participant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("case_packages.id", ondelete="CASCADE"))
    participant_id: Mapped[str] = mapped_column(String(64))
    participant_type: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128))
    public_profile: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class RoleMaterialModel(Base):
    __tablename__ = "role_materials"
    __table_args__ = (UniqueConstraint("package_id", "material_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("case_packages.id", ondelete="CASCADE"))
    material_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class LegalSourceModel(Base):
    __tablename__ = "legal_sources"
    __table_args__ = (UniqueConstraint("package_id", "source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("case_packages.id", ondelete="CASCADE"))
    source_id: Mapped[str] = mapped_column(String(128))
    instrument_title: Mapped[str] = mapped_column(String(255))
    article_number: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class CourtSessionModel(Base):
    __tablename__ = "court_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    package_id: Mapped[int] = mapped_column(ForeignKey("case_packages.id", ondelete="RESTRICT"))
    user_role: Mapped[str] = mapped_column(String(32))
    phase: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="active")
    turns_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    package: Mapped[CasePackageModel] = relationship(back_populates="sessions")
    events: Mapped[list[SessionEventModel]] = relationship(cascade="all, delete-orphan")
    evidence_submissions: Mapped[list[EvidenceSubmissionModel]] = relationship(
        cascade="all, delete-orphan"
    )
    agent_traces: Mapped[list[AgentTraceModel]] = relationship(cascade="all, delete-orphan")


class SessionEventModel(Base):
    __tablename__ = "session_events"
    __table_args__ = (UniqueConstraint("session_id", "sequence_number"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("court_sessions.id", ondelete="CASCADE"), index=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String(64))
    actor_role: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EvidenceSubmissionModel(Base):
    __tablename__ = "evidence_submissions"
    __table_args__ = (UniqueConstraint("session_id", "evidence_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("court_sessions.id", ondelete="CASCADE"), index=True
    )
    evidence_id: Mapped[str] = mapped_column(String(64))
    submitted_by: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="submitted")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentTraceModel(Base):
    __tablename__ = "agent_traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(
        ForeignKey("court_sessions.id", ondelete="CASCADE"), index=True
    )
    actor_role: Mapped[str] = mapped_column(String(32))
    participant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), index=True)
    repair_count: Mapped[int] = mapped_column(Integer, default=0)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_cny: Mapped[float] = mapped_column(Float, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
