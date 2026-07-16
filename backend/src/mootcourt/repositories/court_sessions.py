from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mootcourt.db.models import (
    CourtSessionModel,
    EvidenceModel,
    EvidenceSubmissionModel,
    ParticipantModel,
    SessionEventModel,
)

CourtSessionRecord = CourtSessionModel
SessionEventRecord = SessionEventModel


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
    ) -> CourtSessionModel:
        model = CourtSessionModel(
            package_id=package_id,
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
        return await self._session.get(CourtSessionModel, session_id)

    # 获取会话（加锁，用于更新）
    async def get_for_update(self, session_id: str) -> CourtSessionModel | None:
        return await self._session.scalar(
            select(CourtSessionModel)
            .where(CourtSessionModel.id == session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
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
