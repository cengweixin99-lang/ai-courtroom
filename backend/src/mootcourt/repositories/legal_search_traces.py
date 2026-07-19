from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mootcourt.db.models import LegalSearchTraceModel


class SqlAlchemyLegalSearchTraceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        package_id: int,
        legal_profile_id: str,
        query: str,
        retrieval_mode: str,
        embedding_version: str | None,
        outcome: str,
        filters: dict[str, Any],
        hits: list[dict[str, Any]],
        latency_ms: int,
    ) -> LegalSearchTraceModel:
        trace = LegalSearchTraceModel(
            package_id=package_id,
            legal_profile_id=legal_profile_id,
            query=query,
            retrieval_mode=retrieval_mode,
            embedding_version=embedding_version,
            outcome=outcome,
            filters=filters,
            hits=hits,
            latency_ms=latency_ms,
        )
        self._session.add(trace)
        await self._session.flush()
        await self._session.refresh(trace)
        return trace

    async def get(self, trace_id: str) -> LegalSearchTraceModel | None:
        return await self._session.get(LegalSearchTraceModel, trace_id)

    async def get_many_for_package(
        self, package_id: int, trace_ids: list[str]
    ) -> list[LegalSearchTraceModel]:
        return list(
            await self._session.scalars(
                select(LegalSearchTraceModel).where(
                    LegalSearchTraceModel.package_id == package_id,
                    LegalSearchTraceModel.id.in_(trace_ids),
                )
            )
        )
