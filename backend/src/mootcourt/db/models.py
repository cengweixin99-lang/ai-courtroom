from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
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
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="published", index=True)
    title: Mapped[str] = mapped_column(String(255))
    jurisdiction: Mapped[str] = mapped_column(String(64))
    law_as_of_date: Mapped[date] = mapped_column(Date)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    case_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    legal_profile: Mapped[dict[str, Any]] = mapped_column(JSON)
    legal_issues: Mapped[dict[str, Any]] = mapped_column(JSON)
    procedure_profile: Mapped[dict[str, Any]] = mapped_column(JSON)
    review_manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    legal_search_traces: Mapped[list[LegalSearchTraceModel]] = relationship(
        cascade="all, delete-orphan"
    )
    access_grants: Mapped[list[CaseAccessGrantModel]] = relationship(cascade="all, delete-orphan")
    import_attempts: Mapped[list[CaseImportAttemptModel]] = relationship(back_populates="package")


class OrganizationModel(Base):
    """Platform authorization boundary; it is independent from courtroom seats."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    memberships: Mapped[list[OrganizationMembershipModel]] = relationship(
        cascade="all, delete-orphan"
    )
    case_access_grants: Mapped[list[CaseAccessGrantModel]] = relationship(
        cascade="all, delete-orphan"
    )


class PlatformUserModel(Base):
    """Local user profile keyed by the immutable Supabase JWT subject."""

    __tablename__ = "platform_users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    auth_subject: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    memberships: Mapped[list[OrganizationMembershipModel]] = relationship(
        cascade="all, delete-orphan"
    )
    owned_sessions: Mapped[list[CourtSessionModel]] = relationship()
    case_import_attempts: Mapped[list[CaseImportAttemptModel]] = relationship(
        back_populates="actor"
    )


class OrganizationMembershipModel(Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("platform_users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32), default="learner")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CaseAccessGrantModel(Base):
    __tablename__ = "case_access_grants"
    __table_args__ = (UniqueConstraint("package_id", "organization_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(
        ForeignKey("case_packages.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    access_level: Mapped[str] = mapped_column(String(32), default="use")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CaseImportAttemptModel(Base):
    """Immutable audit record for successful and rejected package uploads."""

    __tablename__ = "case_import_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    package_id: Mapped[int | None] = mapped_column(
        ForeignKey("case_packages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_filename: Mapped[str] = mapped_column(String(255))
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    archive_size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    actor: Mapped[PlatformUserModel | None] = relationship(back_populates="case_import_attempts")
    package: Mapped[CasePackageModel | None] = relationship(back_populates="import_attempts")


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
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    user_role: Mapped[str] = mapped_column(String(32))
    phase: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="active")
    turns_used: Mapped[int] = mapped_column(Integer, default=0)
    active_agent_invocation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active_agent_lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active_agent_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
    agent_invocations: Mapped[list[AgentInvocationModel]] = relationship(
        cascade="all, delete-orphan"
    )
    procedural_requests: Mapped[list[ProceduralRequestModel]] = relationship(
        cascade="all, delete-orphan"
    )
    participant_statement_traces: Mapped[list[ParticipantStatementTraceModel]] = relationship(
        cascade="all, delete-orphan"
    )
    court_reviews: Mapped[list[CourtReviewModel]] = relationship(cascade="all, delete-orphan")


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


class EvidenceAgendaModel(Base):
    """记录每项已提交证据在对方席位的逐证据回应状态。"""

    __tablename__ = "evidence_agenda_items"
    __table_args__ = (UniqueConstraint("session_id", "evidence_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("court_sessions.id", ondelete="CASCADE"), index=True
    )
    phase: Mapped[str] = mapped_column(String(64))
    evidence_id: Mapped[str] = mapped_column(String(64))
    submitted_by: Mapped[str] = mapped_column(String(32))
    responding_role: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    submission_event_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_event_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    challenge_dimensions: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
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
    output_normalized: Mapped[bool] = mapped_column(Boolean, default=False)
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


class AgentInvocationModel(Base):
    __tablename__ = "agent_invocations"
    __table_args__ = (UniqueConstraint("session_id", "idempotency_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(
        ForeignKey("court_sessions.id", ondelete="CASCADE"), index=True
    )
    operation: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    lease_token: Mapped[str] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class LegalSearchTraceModel(Base):
    __tablename__ = "legal_search_traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    package_id: Mapped[int] = mapped_column(
        ForeignKey("case_packages.id", ondelete="CASCADE"), index=True
    )
    legal_profile_id: Mapped[str] = mapped_column(String(128))
    query: Mapped[str] = mapped_column(String(500))
    retrieval_mode: Mapped[str] = mapped_column(String(32))
    embedding_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outcome: Mapped[str] = mapped_column(String(64), index=True)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON)
    hits: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProceduralRequestModel(Base):
    __tablename__ = "procedural_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(
        ForeignKey("court_sessions.id", ondelete="CASCADE"), index=True
    )
    request_type: Mapped[str] = mapped_column(String(48))
    raised_by: Mapped[str] = mapped_column(String(32))
    event_sequence_number: Mapped[int] = mapped_column(Integer)
    target_event_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON)
    challenge_dimensions: Mapped[list[str]] = mapped_column(JSON)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(48), index=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_event_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ParticipantStatementTraceModel(Base):
    __tablename__ = "participant_statement_traces"
    __table_args__ = (UniqueConstraint("session_id", "event_sequence_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(
        ForeignKey("court_sessions.id", ondelete="CASCADE"), index=True
    )
    participant_id: Mapped[str] = mapped_column(String(64))
    actor_role: Mapped[str] = mapped_column(String(32))
    event_sequence_number: Mapped[int] = mapped_column(Integer)
    answer: Mapped[str] = mapped_column(Text)
    supported_statement_ids: Mapped[list[str]] = mapped_column(JSON)
    related_fact_ids: Mapped[list[str]] = mapped_column(JSON)
    consistency_status: Mapped[str] = mapped_column(String(48))
    new_statement: Mapped[bool] = mapped_column(default=False)
    refused_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_status: Mapped[str | None] = mapped_column(String(48), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_event_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CourtReviewModel(Base):
    __tablename__ = "court_reviews"
    __table_args__ = (
        UniqueConstraint("session_id"),
        UniqueConstraint("session_id", "event_sequence_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(
        ForeignKey("court_sessions.id", ondelete="CASCADE"), index=True
    )
    event_sequence_number: Mapped[int] = mapped_column(Integer)
    legal_search_trace_ids: Mapped[list[str]] = mapped_column(JSON)
    report: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CourtReviewEvaluationModel(Base):
    """保存独立模型教学点评，避免改写已冻结的确定性复盘快照。"""

    __tablename__ = "court_review_evaluations"
    __table_args__ = (UniqueConstraint("review_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    review_id: Mapped[str] = mapped_column(
        ForeignKey("court_reviews.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("court_sessions.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    report: Mapped[dict[str, Any]] = mapped_column(JSON)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_cny: Mapped[float] = mapped_column(default=0)
    repair_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
