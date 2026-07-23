from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mootcourt.db.models import (
    CourtReviewEvaluationModel,
    CourtReviewModel,
    CourtSessionModel,
    EvidenceAgendaModel,
    EvidenceModel,
    EvidenceSubmissionModel,
    FactModel,
    OrganizationMembershipModel,
    ParticipantModel,
    ParticipantStatementTraceModel,
    ProceduralRequestModel,
    SessionEventModel,
)

CourtSessionRecord = CourtSessionModel
SessionEventRecord = SessionEventModel
ProceduralRequestRecord = ProceduralRequestModel
ParticipantStatementTraceRecord = ParticipantStatementTraceModel
CourtReviewRecord = CourtReviewModel
CourtReviewEvaluationRecord = CourtReviewEvaluationModel
EvidenceAgendaRecord = EvidenceAgendaModel


class SqlAlchemyCourtSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # 创建会话（含初始事件）
    async def add_session(
        self,
        package_id: int,
        user_role: str,
        phase: str,
        initial_event_payload: dict[str, Any],
        owner_user_id: int | None = None,
    ) -> CourtSessionModel:
        model = CourtSessionModel(
            package_id=package_id,
            owner_user_id=owner_user_id,
            user_role=user_role,
            phase=phase,
            status="active",
            turns_used=0,
        )
        # 自动创建首个事件： session_created
        model.events.append(
            SessionEventModel(
                sequence_number=1,
                phase=phase,
                actor_role="controller",
                action="session_created",
                payload=initial_event_payload,
            )
        )
        self._session.add(model)
        await self._session.flush()  # 写入数据库，获取自增 ID
        await self._session.refresh(model)  # 刷新模型（获取完整数据）
        return model

    # 获取会话
    async def get(self, session_id: str) -> CourtSessionModel | None:
        return cast(
            CourtSessionModel | None, await self._session.get(CourtSessionModel, session_id)
        )

    async def list_for_user(
        self,
        user_id: int,
        *,
        managed_organization_ids: set[str] | None = None,
        include_archived: bool = False,
    ) -> list[CourtSessionModel]:
        """List resumable sessions without exposing another learner's private sessions."""
        query = select(CourtSessionModel).order_by(CourtSessionModel.updated_at.desc())
        access_filter = CourtSessionModel.owner_user_id == user_id
        if managed_organization_ids:
            # 教师和管理员只能查看与自己管理组织重叠的会话所有者。
            managed_owner = exists().where(
                OrganizationMembershipModel.user_id == CourtSessionModel.owner_user_id,
                OrganizationMembershipModel.organization_id.in_(managed_organization_ids),
            )
            access_filter = or_(access_filter, managed_owner)
        query = query.where(access_filter)
        if not include_archived:
            query = query.where(CourtSessionModel.status != "archived")
        return list(await self._session.scalars(query))

    # 获取会话（加锁，用于更新）
    async def get_for_update(self, session_id: str) -> CourtSessionModel | None:
        return cast(
            CourtSessionModel | None,
            await self._session.scalar(
                select(CourtSessionModel)
                .where(CourtSessionModel.id == session_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ),
        )

    # 获取已提交证据 id 列表
    async def submitted_ids(self, session_id: str) -> list[str]:
        return list(
            await self._session.scalars(
                select(EvidenceSubmissionModel.evidence_id)
                .where(EvidenceSubmissionModel.session_id == session_id)
                .order_by(EvidenceSubmissionModel.id)
            )
        )

    async def list_evidence_submissions(self, session_id: str) -> list[EvidenceSubmissionModel]:
        return list(
            await self._session.scalars(
                select(EvidenceSubmissionModel)
                .where(EvidenceSubmissionModel.session_id == session_id)
                .order_by(EvidenceSubmissionModel.id)
            )
        )

    async def list_package_evidence(self, package_id: int) -> list[EvidenceModel]:
        return list(
            await self._session.scalars(
                select(EvidenceModel)
                .where(EvidenceModel.package_id == package_id)
                .order_by(EvidenceModel.evidence_id)
            )
        )

    async def list_package_facts(self, package_id: int) -> list[FactModel]:
        return list(
            await self._session.scalars(
                select(FactModel)
                .where(FactModel.package_id == package_id)
                .order_by(FactModel.fact_id)
            )
        )

    # 获取会话事件列表
    async def list_events(self, session_id: str) -> list[SessionEventModel]:
        return list(
            await self._session.scalars(
                select(SessionEventModel)
                .where(SessionEventModel.session_id == session_id)
                .order_by(SessionEventModel.sequence_number)
            )
        )

    async def list_recent_events(self, session_id: str, limit: int = 20) -> list[SessionEventModel]:
        rows = list(
            await self._session.scalars(
                select(SessionEventModel)
                .where(SessionEventModel.session_id == session_id)
                .order_by(SessionEventModel.sequence_number.desc())
                .limit(limit)
            )
        )
        # 数据库倒序取最近 N 条更高效，交给模型前恢复为庭审发生顺序。
        rows.reverse()
        return rows

    async def get_event_by_sequence(
        self, session_id: str, sequence_number: int
    ) -> SessionEventModel | None:
        return cast(
            SessionEventModel | None,
            await self._session.scalar(
                select(SessionEventModel).where(
                    SessionEventModel.session_id == session_id,
                    SessionEventModel.sequence_number == sequence_number,
                )
            ),
        )

    async def earlier_question_events(
        self, session_id: str, before_sequence: int
    ) -> list[SessionEventModel]:
        return list(
            await self._session.scalars(
                select(SessionEventModel)
                .where(
                    SessionEventModel.session_id == session_id,
                    SessionEventModel.sequence_number < before_sequence,
                    SessionEventModel.action == "question_participant",
                )
                .order_by(SessionEventModel.sequence_number)
            )
        )

    # 按 ID 查询证据
    async def evidence_by_ids(
        self, package_id: int, evidence_ids: list[str]
    ) -> list[EvidenceModel]:
        return list(
            await self._session.scalars(
                select(EvidenceModel).where(
                    EvidenceModel.package_id == package_id,
                    EvidenceModel.evidence_id.in_(evidence_ids),
                )
            )
        )

    async def submitted_ids_from(self, session_id: str, evidence_ids: list[str]) -> set[str]:
        return set(
            await self._session.scalars(
                select(EvidenceSubmissionModel.evidence_id).where(
                    EvidenceSubmissionModel.session_id == session_id,
                    EvidenceSubmissionModel.evidence_id.in_(evidence_ids),
                )
            )
        )

    async def participant_exists(self, package_id: int, participant_id: str) -> bool:
        value = await self._session.scalar(
            select(ParticipantModel.id).where(
                ParticipantModel.package_id == package_id,
                ParticipantModel.participant_id == participant_id,
            )
        )
        return value is not None

    # 添加证据提交记录
    def add_evidence_submissions(
        self, session_id: str, evidence_ids: list[str], submitted_by: str
    ) -> None:
        self._session.add_all(
            [
                EvidenceSubmissionModel(
                    session_id=session_id,
                    evidence_id=evidence_id,
                    submitted_by=submitted_by,
                    status="submitted",
                )
                for evidence_id in evidence_ids
            ]
        )

    def add_evidence_agenda_items(
        self,
        *,
        session_id: str,
        phase: str,
        evidence_ids: list[str],
        submitted_by: str,
        responding_role: str,
        submission_event_sequence: int | None,
    ) -> None:
        self._session.add_all(
            [
                EvidenceAgendaModel(
                    session_id=session_id,
                    phase=phase,
                    evidence_id=evidence_id,
                    submitted_by=submitted_by,
                    responding_role=responding_role,
                    status="pending",
                    submission_event_sequence=submission_event_sequence,
                    challenge_dimensions=[],
                )
                for evidence_id in evidence_ids
            ]
        )

    async def list_evidence_agenda(
        self,
        session_id: str,
        *,
        phase: str | None = None,
        responding_role: str | None = None,
        status: str | None = None,
    ) -> list[EvidenceAgendaModel]:
        query = select(EvidenceAgendaModel).where(EvidenceAgendaModel.session_id == session_id)
        if phase is not None:
            query = query.where(EvidenceAgendaModel.phase == phase)
        if responding_role is not None:
            query = query.where(EvidenceAgendaModel.responding_role == responding_role)
        if status is not None:
            query = query.where(EvidenceAgendaModel.status == status)
        return list(await self._session.scalars(query.order_by(EvidenceAgendaModel.id)))

    async def evidence_agenda_for_update(
        self, session_id: str, evidence_ids: list[str]
    ) -> list[EvidenceAgendaModel]:
        return list(
            await self._session.scalars(
                select(EvidenceAgendaModel)
                .where(
                    EvidenceAgendaModel.session_id == session_id,
                    EvidenceAgendaModel.evidence_id.in_(evidence_ids),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    async def record_evidence_agenda_response(
        self,
        rows: list[EvidenceAgendaModel],
        *,
        status: str,
        response_action: str,
        response_event_sequence: int,
        challenge_dimensions: list[str],
    ) -> None:
        for row in rows:
            row.status = status
            row.response_action = response_action
            row.response_event_sequence = response_event_sequence
            row.challenge_dimensions = challenge_dimensions
        await self._session.flush()

    async def defer_pending_evidence_agenda(
        self,
        *,
        session_id: str,
        phase: str,
        responding_role: str,
        response_event_sequence: int,
    ) -> None:
        rows = await self.list_evidence_agenda(
            session_id,
            phase=phase,
            responding_role=responding_role,
            status="pending",
        )
        await self.record_evidence_agenda_response(
            rows,
            status="deferred",
            response_action="complete_phase",
            response_event_sequence=response_event_sequence,
            challenge_dimensions=[],
        )

    async def add_procedural_request(
        self,
        *,
        session_id: str,
        request_type: str,
        raised_by: str,
        event_sequence_number: int,
        target_event_sequence: int | None,
        evidence_ids: list[str],
        challenge_dimensions: list[str],
        content: str,
        status: str,
    ) -> ProceduralRequestModel:
        model = ProceduralRequestModel(
            session_id=session_id,
            request_type=request_type,
            raised_by=raised_by,
            event_sequence_number=event_sequence_number,
            target_event_sequence=target_event_sequence,
            evidence_ids=evidence_ids,
            challenge_dimensions=challenge_dimensions,
            content=content,
            status=status,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return model

    async def list_procedural_requests(self, session_id: str) -> list[ProceduralRequestModel]:
        return list(
            await self._session.scalars(
                select(ProceduralRequestModel)
                .where(ProceduralRequestModel.session_id == session_id)
                .order_by(ProceduralRequestModel.event_sequence_number)
            )
        )

    async def get_procedural_request_for_update(
        self, session_id: str, request_id: str
    ) -> ProceduralRequestModel | None:
        return cast(
            ProceduralRequestModel | None,
            await self._session.scalar(
                select(ProceduralRequestModel)
                .where(
                    ProceduralRequestModel.id == request_id,
                    ProceduralRequestModel.session_id == session_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            ),
        )

    async def resolve_procedural_request(
        self,
        model: ProceduralRequestModel,
        *,
        resolution: str,
        reason: str,
        event_sequence_number: int,
        resolved_at: datetime,
    ) -> None:
        model.resolution = resolution
        model.resolution_reason = reason
        model.resolution_event_sequence = event_sequence_number
        model.resolved_at = resolved_at
        model.status = "resolved"
        await self._session.flush()

    async def add_participant_statement_trace(
        self,
        *,
        session_id: str,
        participant_id: str,
        actor_role: str,
        event_sequence_number: int,
        answer: str,
        supported_statement_ids: list[str],
        related_fact_ids: list[str],
        consistency_status: str,
        new_statement: bool,
        refused_reason: str | None,
    ) -> ParticipantStatementTraceModel:
        model = ParticipantStatementTraceModel(
            session_id=session_id,
            participant_id=participant_id,
            actor_role=actor_role,
            event_sequence_number=event_sequence_number,
            answer=answer,
            supported_statement_ids=supported_statement_ids,
            related_fact_ids=related_fact_ids,
            consistency_status=consistency_status,
            new_statement=new_statement,
            refused_reason=refused_reason,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return model

    async def list_participant_statement_traces(
        self, session_id: str
    ) -> list[ParticipantStatementTraceModel]:
        return list(
            await self._session.scalars(
                select(ParticipantStatementTraceModel)
                .where(ParticipantStatementTraceModel.session_id == session_id)
                .order_by(ParticipantStatementTraceModel.event_sequence_number)
            )
        )

    async def get_participant_statement_trace_for_update(
        self, session_id: str, trace_id: str
    ) -> ParticipantStatementTraceModel | None:
        return cast(
            ParticipantStatementTraceModel | None,
            await self._session.scalar(
                select(ParticipantStatementTraceModel)
                .where(
                    ParticipantStatementTraceModel.id == trace_id,
                    ParticipantStatementTraceModel.session_id == session_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            ),
        )

    async def resolve_participant_statement_trace(
        self,
        model: ParticipantStatementTraceModel,
        *,
        resolution: str,
        reason: str,
        event_sequence_number: int,
        reviewed_at: datetime,
    ) -> None:
        model.review_status = resolution
        model.review_reason = reason
        model.review_event_sequence = event_sequence_number
        model.reviewed_at = reviewed_at
        await self._session.flush()

    async def add_court_review(
        self,
        *,
        review_id: str,
        session_id: str,
        event_sequence_number: int,
        legal_search_trace_ids: list[str],
        report: dict[str, Any],
        created_at: datetime,
    ) -> CourtReviewModel:
        model = CourtReviewModel(
            id=review_id,
            session_id=session_id,
            event_sequence_number=event_sequence_number,
            legal_search_trace_ids=legal_search_trace_ids,
            report=report,
            created_at=created_at,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return model

    async def get_court_review(self, session_id: str) -> CourtReviewModel | None:
        return cast(
            CourtReviewModel | None,
            await self._session.scalar(
                select(CourtReviewModel).where(CourtReviewModel.session_id == session_id)
            ),
        )

    async def add_court_review_evaluation(
        self,
        *,
        evaluation_id: str,
        review_id: str,
        session_id: str,
        provider: str,
        model: str,
        report: dict[str, Any],
        input_tokens: int,
        output_tokens: int,
        estimated_cost_cny: float,
        repair_count: int,
        created_at: datetime,
    ) -> CourtReviewEvaluationModel:
        evaluation = CourtReviewEvaluationModel(
            id=evaluation_id,
            review_id=review_id,
            session_id=session_id,
            provider=provider,
            model=model,
            report=report,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_cny=estimated_cost_cny,
            repair_count=repair_count,
            created_at=created_at,
        )
        self._session.add(evaluation)
        await self._session.flush()
        await self._session.refresh(evaluation)
        return evaluation

    async def get_court_review_evaluation(
        self, review_id: str
    ) -> CourtReviewEvaluationModel | None:
        return cast(
            CourtReviewEvaluationModel | None,
            await self._session.scalar(
                select(CourtReviewEvaluationModel).where(
                    CourtReviewEvaluationModel.review_id == review_id
                )
            ),
        )

    async def next_event_sequence(self, session_id: str) -> int:
        current = await self._session.scalar(
            select(func.coalesce(func.max(SessionEventModel.sequence_number), 0)).where(
                SessionEventModel.session_id == session_id
            )
        )
        return (current or 0) + 1

    # 添加会话事件
    async def add_event(
        self,
        session_id: str,
        sequence_number: int,
        phase: str,
        actor_role: str,
        action: str,
        payload: dict[str, Any],
    ) -> SessionEventModel:
        event = SessionEventModel(
            session_id=session_id,
            sequence_number=sequence_number,
            phase=phase,
            actor_role=actor_role,
            action=action,
            payload=payload,
        )
        self._session.add(event)
        await self._session.flush()
        await self._session.refresh(event)
        return event

    async def flush_session(self, model: CourtSessionModel) -> None:
        await self._session.flush()
        await self._session.refresh(model)
