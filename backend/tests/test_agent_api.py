from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette.sse import AppStatus

from mootcourt.agents.openai_compatible import AgentProviderError
from mootcourt.agents.providers import (
    CONTROLLED_CITATION_PROTOCOL,
    AgentProviderRequest,
    AgentProviderResult,
    FakeAgentProvider,
)
from mootcourt.api.dependencies import (
    get_agent_provider,
    get_unit_of_work,
    require_authenticated_principal,
)
from mootcourt.core.auth import AuthenticatedPrincipal
from mootcourt.core.config import Settings, get_settings
from mootcourt.domain.courtroom import CourtAction, CourtPhase
from mootcourt.main import app
from mootcourt.repositories.agent_traces import SessionAgentUsage
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.agents import (
    AgentRole,
    AgentStatementCitation,
    AgentTurnRequest,
    Certainty,
    ClaimType,
    WitnessOutput,
)
from mootcourt.services.agent_context import build_agent_context
from mootcourt.services.agent_invocations import acquire_agent_invocation
from mootcourt.services.agent_turns import (
    AgentTurnServiceError,
    _normalize_participant_output,
    _validate_budget_before_call,
)
from mootcourt.services.case_importer import import_case_package

CASE_PACKAGE = Path(__file__).parents[2] / "data" / "authoring" / "CASE-001"


def test_participant_normalization_keeps_auditable_partial_refusal() -> None:
    output = WitnessOutput(
        answer="我只能回答其中一部分。",
        supported_by_statement_ids=["W01-S01"],
        citations=[AgentStatementCitation(statement_id="W01-S01", quote="我看见被告人离开现场")],
        certainty=Certainty.MEDIUM,
        refused_reason="其余问题超出既有陈述范围",
    )

    normalized, changed = _normalize_participant_output(output)

    assert changed is True
    assert isinstance(normalized, WitnessOutput)
    assert normalized.answer == ("我看见被告人离开现场 对超出既有陈述范围的问题，我无法回答。")


class CapturingFakeProvider(FakeAgentProvider):
    def __init__(self) -> None:
        self.requests: list[AgentProviderRequest] = []

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        self.requests.append(request)
        return await super().generate(request)


class BlockingFakeProvider(FakeAgentProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return await super().generate(request)


class CancelledFakeProvider(FakeAgentProvider):
    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        raise asyncio.CancelledError


class InvalidOutputProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "invalid-test"

    @property
    def model_name(self) -> str:
        return "invalid-output"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        self.calls += 1
        return AgentProviderResult(
            output={"kind": "witness"},
            provider=self.provider_name,
            model=self.model_name,
            input_tokens=10,
            output_tokens=2,
            estimated_cost_cny=0.01,
        )


class ForbiddenEvidenceProvider:
    @property
    def provider_name(self) -> str:
        return "forbidden-test"

    @property
    def model_name(self) -> str:
        return "forbidden-evidence"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        return AgentProviderResult(
            output={
                "kind": "advocate",
                "speaker_role": request.context.actor_role.value,
                "speech": "引用不可见证据。",
                "claims": [
                    {
                        "text": "越权主张",
                        "claim_type": "supported_fact",
                        "fact_ids": ["F01"],
                        "citations": [{"evidence_id": "E99", "quote": "不存在的证据原文片段"}],
                    }
                ],
                "requested_action": request.context.action.value,
                "target_id": None,
            },
            provider=self.provider_name,
            model=self.model_name,
        )


class WrongTaskEvidenceProvider:
    @property
    def provider_name(self) -> str:
        return "wrong-task-evidence-test"

    @property
    def model_name(self) -> str:
        return "wrong-task-evidence"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        evidence = next(item for item in request.context.evidence if item.id == "E02")
        claim_text = "本方引用了本轮未选定的证据。"
        return AgentProviderResult(
            output={
                "kind": "advocate",
                "speaker_role": request.context.actor_role.value,
                "speech": claim_text,
                "claims": [
                    {
                        "text": claim_text,
                        "claim_type": "supported_fact",
                        "fact_ids": ["F02"],
                        "citations": [{"evidence_id": evidence.id, "quote": evidence.content[:20]}],
                    }
                ],
                "requested_action": request.context.action.value,
                "target_id": None,
            },
            provider=self.provider_name,
            model=self.model_name,
        )


class SubsetEvidenceProvider:
    @property
    def provider_name(self) -> str:
        return "subset-evidence-test"

    @property
    def model_name(self) -> str:
        return "subset-evidence"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        evidence = next(item for item in request.context.evidence if item.id == "E01")
        claim_text = "本方仅针对证据 E01 发表意见。"
        return AgentProviderResult(
            output={
                "kind": "advocate",
                "speaker_role": request.context.actor_role.value,
                "speech": claim_text,
                "claims": [
                    {
                        "text": claim_text,
                        "claim_type": "disputed_fact",
                        "fact_ids": ["F01"],
                        "citations": [{"evidence_id": evidence.id, "quote": evidence.content[:20]}],
                    }
                ],
                "requested_action": request.context.action.value,
                "target_id": None,
            },
            provider=self.provider_name,
            model=self.model_name,
        )


class FactPositionProvider:
    """按指定立场对 F13 发表主张，引用本轮任务选定的证据。"""

    def __init__(self, claim_type: str) -> None:
        self._claim_type = claim_type

    @property
    def provider_name(self) -> str:
        return "fact-position-test"

    @property
    def model_name(self) -> str:
        return "fact-position"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        evidence_id = request.context.task.evidence_ids[0]
        evidence = next(item for item in request.context.evidence if item.id == evidence_id)
        claim_text = "本方就林舟取走相机的主观目的发表意见。"
        return AgentProviderResult(
            output={
                "kind": "advocate",
                "speaker_role": request.context.actor_role.value,
                "speech": claim_text,
                "claims": [
                    {
                        "text": claim_text,
                        "claim_type": self._claim_type,
                        "fact_ids": ["F13"],
                        "citations": [{"evidence_id": evidence.id, "quote": evidence.content[:20]}],
                    }
                ],
                "requested_action": request.context.action.value,
                "target_id": None,
            },
            provider=self.provider_name,
            model=self.model_name,
        )


class LegalCitationProvider:
    """在发言中引用指定法条，用于验证白名单法源校验。"""

    def __init__(self, citation: str) -> None:
        self._citation = citation

    @property
    def provider_name(self) -> str:
        return "legal-citation-test"

    @property
    def model_name(self) -> str:
        return "legal-citation"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        evidence_id = request.context.task.evidence_ids[0]
        evidence = next(item for item in request.context.evidence if item.id == evidence_id)
        claim_text = f"根据{self._citation}，本方就林舟取走相机的主观目的发表意见。"
        return AgentProviderResult(
            output={
                "kind": "advocate",
                "speaker_role": request.context.actor_role.value,
                "speech": claim_text,
                "claims": [
                    {
                        "text": claim_text,
                        "claim_type": "supported_fact",
                        "fact_ids": ["F13"],
                        "citations": [{"evidence_id": evidence.id, "quote": evidence.content[:20]}],
                    }
                ],
                "requested_action": request.context.action.value,
                "target_id": None,
            },
            provider=self.provider_name,
            model=self.model_name,
        )


class UnverifiedEvidenceQuoteProvider:
    @property
    def provider_name(self) -> str:
        return "unverified-quote-test"

    @property
    def model_name(self) -> str:
        return "unverified-quote"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        claim_text = "证据能够证明全部指控事实。"
        return AgentProviderResult(
            output={
                "kind": "advocate",
                "speaker_role": request.context.actor_role.value,
                "speech": claim_text,
                "claims": [
                    {
                        "text": claim_text,
                        "claim_type": "supported_fact",
                        "fact_ids": ["F01"],
                        "citations": [{"evidence_id": "E01", "quote": "这段原文并不存在"}],
                    }
                ],
                "requested_action": request.context.action.value,
                "target_id": None,
            },
            provider=self.provider_name,
            model=self.model_name,
        )


class AnchoredEvidenceProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "anchored-evidence-test"

    @property
    def model_name(self) -> str:
        return "anchored-evidence"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        self.calls += 1
        claim_text = "本方依据受控证据锚点提出主张。"
        return AgentProviderResult(
            output={
                "kind": "advocate",
                "speaker_role": request.context.actor_role.value,
                "speech": claim_text,
                "claims": [
                    {
                        "text": claim_text,
                        "claim_type": "supported_fact",
                        "fact_ids": ["F01"],
                        "citations": [{"anchor_id": "E01:content:1"}],
                    }
                ],
                "requested_action": request.context.action.value,
                "target_id": None,
            },
            provider=self.provider_name,
            model=self.model_name,
        )


class JoinedEvidenceNotesProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "joined-evidence-notes-test"

    @property
    def model_name(self) -> str:
        return "joined-evidence-notes"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        self.calls += 1
        claim_text = "手机号下单不能单独排除他人操作，寄存柜开启程序也需要核对。"
        return AgentProviderResult(
            output={
                "kind": "advocate",
                "speaker_role": request.context.actor_role.value,
                "speech": claim_text,
                "claims": [
                    {
                        "text": claim_text,
                        "claim_type": "disputed_fact",
                        "fact_ids": ["F10"],
                        "citations": [
                            {
                                "evidence_id": "E07",
                                "quote": (
                                    "需核对开启程序、见证人和连续保管情况\n"
                                    "手机号下单不能单独排除他人操作"
                                ),
                            }
                        ],
                    }
                ],
                "requested_action": request.context.action.value,
                "target_id": None,
            },
            provider=self.provider_name,
            model=self.model_name,
        )


class ControlledLegacyQuoteProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "controlled-legacy-quote-test"

    @property
    def model_name(self) -> str:
        return "controlled-legacy-quote"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        self.calls += 1
        evidence = next(item for item in request.context.evidence if item.id == "E01")
        claim_text = "该输出错误地绕过受控锚点直接填写引文。"
        return AgentProviderResult(
            output={
                "kind": "advocate",
                "speaker_role": request.context.actor_role.value,
                "speech": claim_text,
                "claims": [
                    {
                        "text": claim_text,
                        "claim_type": "supported_fact",
                        "fact_ids": ["F01"],
                        "citations": [{"evidence_id": "E01", "quote": evidence.content}],
                    }
                ],
                "requested_action": request.context.action.value,
                "target_id": None,
            },
            provider=self.provider_name,
            model=self.model_name,
            citation_protocol=CONTROLLED_CITATION_PROTOCOL,
        )


class UnverifiedStatementQuoteProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "unverified-statement-test"

    @property
    def model_name(self) -> str:
        return "unverified-statement"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        self.calls += 1
        return AgentProviderResult(
            output={
                "kind": "witness",
                "answer": "这段原文并不存在",
                "supported_by_statement_ids": ["W01-S01"],
                "citations": [{"statement_id": "W01-S01", "quote": "这段原文并不存在"}],
                "certainty": "high",
                "refused_reason": None,
            },
            provider=self.provider_name,
            model=self.model_name,
        )


class ParaphrasedParticipantProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "participant-normalization-test"

    @property
    def model_name(self) -> str:
        return "paraphrased-participant"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        self.calls += 1
        assert request.context.participant is not None
        statement = request.context.participant.statements[0]
        return AgentProviderResult(
            output={
                "kind": "witness",
                "answer": "我看到了相关情况。",
                "supported_by_statement_ids": [statement.id],
                "citations": [{"statement_id": statement.id, "quote": statement.text}],
                "certainty": "high",
                "refused_reason": None,
            },
            provider=self.provider_name,
            model=self.model_name,
        )


class EmptyAnswerRefusalProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "explicit-refusal-normalization-test"

    @property
    def model_name(self) -> str:
        return "empty-answer-refusal"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        self.calls += 1
        return AgentProviderResult(
            output={
                "kind": "witness",
                "answer": "",
                "supported_by_statement_ids": [],
                "citations": [],
                "certainty": "high",
                "refused_reason": "该问题超出我的既有陈述范围，无法回答。",
            },
            provider=self.provider_name,
            model=self.model_name,
        )


class ExplodingProvider:
    @property
    def provider_name(self) -> str:
        return "error-test"

    @property
    def model_name(self) -> str:
        return "provider-error"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        raise TimeoutError("provider timed out")


class UnrelatedFactProvider:
    @property
    def provider_name(self) -> str:
        return "unrelated-fact-test"

    @property
    def model_name(self) -> str:
        return "unrelated-fact"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        evidence = next(item for item in request.context.evidence if item.id == "E01")
        claim_text = "本方认为该证据能够证明案发现场勘验情况。"
        return AgentProviderResult(
            output={
                "kind": "advocate",
                "speaker_role": request.context.actor_role.value,
                "speech": claim_text,
                "claims": [
                    {
                        "text": claim_text,
                        "claim_type": "supported_fact",
                        "fact_ids": ["F03"],
                        "citations": [{"evidence_id": evidence.id, "quote": evidence.content[:20]}],
                    }
                ],
                "requested_action": request.context.action.value,
                "target_id": None,
            },
            provider=self.provider_name,
            model=self.model_name,
        )


class RepairFailureProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "repair-failure-test"

    @property
    def model_name(self) -> str:
        return "repair-failure"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        self.calls += 1
        if self.calls == 1:
            return AgentProviderResult(
                output={"kind": "witness"},
                provider=self.provider_name,
                model=self.model_name,
                input_tokens=10,
                output_tokens=2,
                estimated_cost_cny=0.01,
            )
        raise AgentProviderError(
            "agent_provider_invalid_response",
            "repair response is not valid JSON",
            input_tokens=20,
            output_tokens=4,
            estimated_cost_cny=0.02,
        )


class ExpensiveProvider(FakeAgentProvider):
    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        result = await super().generate(request)
        return AgentProviderResult(
            output=result.output,
            provider="expensive-test",
            model="over-budget",
            input_tokens=1_000,
            output_tokens=500,
            estimated_cost_cny=21,
        )


class NewDefendantStatementProvider:
    @property
    def provider_name(self) -> str:
        return "new-statement-test"

    @property
    def model_name(self) -> str:
        return "new-statement"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        return AgentProviderResult(
            output={
                "kind": "defendant",
                "answer": "我在本庭补充说明，当晚还曾在门口停留。",
                "supported_by_statement_ids": [],
                "new_statement": True,
                "certainty": "medium",
                "refused_reason": None,
            },
            provider=self.provider_name,
            model=self.model_name,
        )


@pytest_asyncio.fixture
async def agent_api_client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        await import_case_package(unit_of_work, CASE_PACKAGE)
        await unit_of_work.commit()

    async def override_unit_of_work() -> AsyncIterator[SqlAlchemyUnitOfWork]:
        async with session_factory() as session:
            unit_of_work = SqlAlchemyUnitOfWork(session)
            try:
                yield unit_of_work
                await unit_of_work.commit()
            except Exception:
                await unit_of_work.rollback()
                raise

    app.dependency_overrides[get_unit_of_work] = override_unit_of_work
    app.dependency_overrides[require_authenticated_principal] = lambda: AuthenticatedPrincipal(
        subject="test-agent-user",
        email="agent@example.test",
        provider_role="authenticated",
        claims={"sub": "test-agent-user"},
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def _create_session(client: AsyncClient, user_role: str = "prosecution") -> str:
    response = await client.post(
        "/api/v1/sessions",
        json={"case_id": "CASE-001", "user_role": user_role},
    )
    assert response.status_code == 201
    session_id = response.json()["session_id"]
    assert isinstance(session_id, str)
    return session_id


async def _advance(client: AsyncClient, session_id: str, count: int) -> None:
    for _ in range(count):
        response = await client.post(
            f"/api/v1/sessions/{session_id}/actions",
            json={"action": "advance_phase"},
        )
        assert response.status_code == 200


def _use_provider(provider: object) -> None:
    app.dependency_overrides[get_agent_provider] = lambda: provider


async def test_advocate_agent_turn_persists_event_and_trace(
    agent_api_client: AsyncClient,
) -> None:
    provider = CapturingFakeProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 2)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={"actor_role": "defense", "action": "make_statement"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["output"]["speaker_role"] == "defense"
    assert body["event"]["actor_role"] == "defense"
    assert body["event"]["payload"]["trace_id"] == body["trace"]["trace_id"]
    assert body["session"]["turns_used"] == 1
    traces = await agent_api_client.get(f"/api/v1/sessions/{session_id}/traces")
    assert traces.status_code == 200
    assert traces.json()[0]["status"] == "succeeded"


async def test_agent_turn_replays_completed_idempotent_request(
    agent_api_client: AsyncClient,
) -> None:
    provider = CapturingFakeProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 2)
    headers = {"Idempotency-Key": "agent-turn-replay-001"}
    payload = {"actor_role": "defense", "action": "make_statement"}

    first = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns", json=payload, headers=headers
    )
    replay = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns", json=payload, headers=headers
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.headers["idempotency-replayed"] == "false"
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.json()["trace"]["trace_id"] == first.json()["trace"]["trace_id"]
    assert replay.json()["event"]["sequence_number"] == first.json()["event"]["sequence_number"]
    assert len(provider.requests) == 1
    traces = await agent_api_client.get(f"/api/v1/sessions/{session_id}/traces")
    assert len(traces.json()) == 1


async def test_idempotency_key_cannot_be_reused_for_different_agent_request(
    agent_api_client: AsyncClient,
) -> None:
    _use_provider(FakeAgentProvider())
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 2)
    headers = {"Idempotency-Key": "agent-turn-conflict-001"}

    first = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={"actor_role": "defense", "action": "make_statement"},
        headers=headers,
    )
    conflict = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "defense",
            "action": "make_statement",
            "instruction": "改成另一项任务",
        },
        headers=headers,
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_key_reused"


async def test_session_lease_blocks_a_second_paid_agent_call(
    agent_api_client: AsyncClient,
) -> None:
    provider = BlockingFakeProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 2)
    payload = {"actor_role": "defense", "action": "make_statement"}

    first_task = asyncio.create_task(
        agent_api_client.post(
            f"/api/v1/sessions/{session_id}/agent-turns",
            json=payload,
            headers={"Idempotency-Key": "concurrent-agent-001"},
        )
    )
    await asyncio.wait_for(provider.started.wait(), timeout=2)
    blocked = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json=payload,
        headers={"Idempotency-Key": "concurrent-agent-002"},
    )
    provider.release.set()
    first = await asyncio.wait_for(first_task, timeout=2)

    assert first.status_code == 200
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "agent_invocation_in_progress"
    assert provider.calls == 1


async def test_expired_session_lease_can_be_replaced(
    agent_api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id = await _create_session(agent_api_client)
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        first = await acquire_agent_invocation(
            unit_of_work,
            session_id,
            "agent_turn",
            "expired-agent-001",
            {"actor_role": "defense"},
            lease_seconds=-1,
        )
        await unit_of_work.commit()
        second = await acquire_agent_invocation(
            unit_of_work,
            session_id,
            "agent_turn",
            "replacement-agent-002",
            {"actor_role": "defense"},
            lease_seconds=900,
        )
        expired = await unit_of_work.agent_invocations.get_for_update(first.invocation_id)

    assert second.invocation_id != first.invocation_id
    assert expired is not None
    assert expired.status == "abandoned"
    assert expired.error_code == "agent_invocation_lease_expired"


async def test_agent_evidence_submission_is_visible_immediately(
    agent_api_client: AsyncClient,
) -> None:
    provider = CapturingFakeProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client, user_role="defense")
    await _advance(agent_api_client, session_id, 3)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "prosecution",
            "action": "submit_evidence",
            "evidence_ids": ["E01"],
        },
    )

    assert response.status_code == 200
    assert response.json()["session"]["submitted_evidence_ids"] == ["E01"]
    assert provider.requests[0].context.task.evidence_ids == ["E01"]
    # 成功响应返回后，新请求必须立即看到已经提交的证据，不允许短暂回退为未提交。
    statuses = await agent_api_client.get(f"/api/v1/sessions/{session_id}/evidence-statuses")
    by_id = {item["evidence_id"]: item for item in statuses.json()}
    assert by_id["E01"]["status"] == "submitted"


async def test_auto_step_waits_for_prosecution_user_and_allows_multiple_statements(
    agent_api_client: AsyncClient,
) -> None:
    provider = CapturingFakeProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client, user_role="prosecution")

    opening = await agent_api_client.post(f"/api/v1/sessions/{session_id}/auto-step")
    assert opening.status_code == 200
    assert opening.json()["status"] == "progressed"
    assert opening.json()["session"]["phase"] == "INDICTMENT_AND_DEFENDANT_STATEMENT"

    waiting = await agent_api_client.post(f"/api/v1/sessions/{session_id}/auto-step")
    assert waiting.json()["status"] == "waiting_for_user"
    assert provider.requests == []

    for content in ("公诉方第一次陈述。", "公诉方补充陈述。"):
        statement = await agent_api_client.post(
            f"/api/v1/sessions/{session_id}/actions",
            json={"action": "make_statement", "content": content},
        )
        assert statement.status_code == 200
        assert statement.json()["session"]["phase"] == "INDICTMENT_AND_DEFENDANT_STATEMENT"

    still_waiting = await agent_api_client.post(f"/api/v1/sessions/{session_id}/auto-step")
    assert still_waiting.json()["status"] == "waiting_for_user"


async def test_completing_user_phase_automatically_runs_defendant_and_advances(
    agent_api_client: AsyncClient,
) -> None:
    provider = CapturingFakeProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client, user_role="prosecution")
    await agent_api_client.post(f"/api/v1/sessions/{session_id}/auto-step")

    completed = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={"action": "complete_phase"},
    )
    assert completed.status_code == 200

    defendant = await agent_api_client.post(f"/api/v1/sessions/{session_id}/auto-step")
    assert defendant.json()["event"]["actor_role"] == "defendant"
    assert defendant.json()["event"]["action"] == "make_statement"

    advanced = await agent_api_client.post(f"/api/v1/sessions/{session_id}/auto-step")
    assert advanced.json()["session"]["phase"] == "COURT_INVESTIGATION"
    next_pause = await agent_api_client.post(f"/api/v1/sessions/{session_id}/auto-step")
    assert next_pause.json()["status"] == "waiting_for_user"


async def test_defense_user_does_not_control_indictment_agents(
    agent_api_client: AsyncClient,
) -> None:
    provider = CapturingFakeProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client, user_role="defense")

    await agent_api_client.post(f"/api/v1/sessions/{session_id}/auto-step")
    prosecution = await agent_api_client.post(f"/api/v1/sessions/{session_id}/auto-step")
    defendant = await agent_api_client.post(f"/api/v1/sessions/{session_id}/auto-step")
    advanced = await agent_api_client.post(f"/api/v1/sessions/{session_id}/auto-step")

    assert prosecution.json()["event"]["actor_role"] == "prosecution"
    assert defendant.json()["event"]["actor_role"] == "defendant"
    assert advanced.json()["session"]["phase"] == "COURT_INVESTIGATION"


async def test_stream_auto_step_emits_temporary_text_before_committed_event(
    agent_api_client: AsyncClient,
) -> None:
    _use_provider(FakeAgentProvider())
    session_id = await _create_session(agent_api_client, user_role="defense")
    opening_headers = {"Idempotency-Key": "stream-step-replay-001"}
    opening = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/auto-step/stream", headers=opening_headers
    )
    assert opening.status_code == 200
    assert "event: step.completed" in opening.text
    replay = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/auto-step/stream", headers=opening_headers
    )
    assert replay.headers["idempotency-replayed"] == "true"
    assert "event: turn.delta" not in replay.text
    opening_events = await agent_api_client.get(f"/api/v1/sessions/{session_id}/events")
    assert len(opening_events.json()) == 2

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/auto-step/stream",
        headers={"Idempotency-Key": "stream-step-agent-002"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    event_names = [
        line.removeprefix("event: ")
        for line in response.text.splitlines()
        if line.startswith("event: ")
    ]
    assert event_names == [
        "step.started",
        "turn.started",
        "turn.delta",
        "turn.validating",
        "step.completed",
    ]
    assert "本方依据证据 E" in response.text
    events = await agent_api_client.get(f"/api/v1/sessions/{session_id}/events")
    assert events.json()[-1]["actor_role"] == "prosecution"


async def test_cancelled_stream_releases_lease_for_same_idempotency_key(
    agent_api_client: AsyncClient,
) -> None:
    # sse-starlette 的退出事件绑定创建它的 event loop，测试隔离时需要重置。
    AppStatus.should_exit_event = None
    _use_provider(CancelledFakeProvider())
    session_id = await _create_session(agent_api_client, user_role="defense")
    await agent_api_client.post(f"/api/v1/sessions/{session_id}/auto-step")
    headers = {"Idempotency-Key": "cancelled-stream-retry-001"}

    interrupted = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/auto-step/stream", headers=headers
    )

    assert interrupted.status_code == 200
    assert "stream_client_disconnected" in interrupted.text

    _use_provider(FakeAgentProvider())
    recovered = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/auto-step/stream", headers=headers
    )

    assert recovered.status_code == 200
    assert "event: step.completed" in recovered.text


async def test_witness_context_excludes_private_and_forbidden_material(
    agent_api_client: AsyncClient,
) -> None:
    provider = CapturingFakeProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 5)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "witness",
            "participant_id": "W01",
            "action": "make_statement",
            "instruction": "请说明你亲眼看到的内容",
        },
    )

    assert response.status_code == 200
    context = provider.requests[0].context
    serialized = context.model_dump_json()
    assert {item.id for item in context.facts} == {"F03", "F07"}
    assert context.evidence == []
    assert context.role_materials == []
    assert "private_background" not in serialized
    assert "forbidden_fact_ids" not in serialized
    assert "F04" not in serialized
    assert response.json()["output"]["supported_by_statement_ids"] == ["W01-S01"]

    traces = await agent_api_client.get(
        f"/api/v1/sessions/{session_id}/participant-statement-traces"
    )
    assert traces.status_code == 200
    trace = traces.json()[0]
    assert trace["event_sequence_number"] == response.json()["event"]["sequence_number"]
    assert trace["participant_id"] == "W01"
    assert trace["supported_statement_ids"] == ["W01-S01"]
    assert trace["related_fact_ids"] == ["F03"]
    assert trace["consistency_status"] == "SUPPORTED_BY_PRIOR_STATEMENT"

    summary = await agent_api_client.get(f"/api/v1/sessions/{session_id}/evidence-fact-summary")
    by_id = {item["fact_id"]: item for item in summary.json()}
    assert by_id["F03"]["appeared_statement_ids"] == ["W01-S01"]


async def test_invalid_output_is_repaired_once_and_only_trace_is_persisted(
    agent_api_client: AsyncClient,
) -> None:
    provider = InvalidOutputProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 5)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "witness",
            "participant_id": "W01",
            "action": "make_statement",
        },
    )

    assert response.status_code == 502
    assert provider.calls == 2
    assert response.json()["status"] == "failed"
    assert response.json()["trace"]["repair_count"] == 1
    assert response.json()["error"]["code"] == "agent_output_invalid"
    events = await agent_api_client.get(f"/api/v1/sessions/{session_id}/events")
    assert len(events.json()) == 6
    traces = await agent_api_client.get(f"/api/v1/sessions/{session_id}/traces")
    assert traces.json()[0]["status"] == "failed"


async def test_agent_output_cannot_cite_invisible_evidence(
    agent_api_client: AsyncClient,
) -> None:
    _use_provider(ForbiddenEvidenceProvider())
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 2)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={"actor_role": "defense", "action": "make_statement"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "agent_output_forbidden"
    events = await agent_api_client.get(f"/api/v1/sessions/{session_id}/events")
    assert len(events.json()) == 3


async def test_agent_output_cannot_cite_evidence_outside_current_task(
    agent_api_client: AsyncClient,
) -> None:
    _use_provider(WrongTaskEvidenceProvider())
    session_id = await _create_session(agent_api_client, user_role="defense")
    await _advance(agent_api_client, session_id, 3)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "prosecution",
            "action": "submit_evidence",
            "evidence_ids": ["E01"],
        },
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "agent_output_forbidden"


async def test_agent_evidence_submission_still_requires_every_selected_item(
    agent_api_client: AsyncClient,
) -> None:
    _use_provider(SubsetEvidenceProvider())
    session_id = await _create_session(agent_api_client, user_role="defense")
    await _advance(agent_api_client, session_id, 3)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "prosecution",
            "action": "submit_evidence",
            "evidence_ids": ["E01", "E02"],
        },
    )

    assert response.status_code == 502
    assert response.json()["error"]["message"] == (
        "agent output does not address every evidence item approved for this turn"
    )


async def test_agent_challenge_may_select_subset_and_records_only_addressed_evidence(
    agent_api_client: AsyncClient,
) -> None:
    _use_provider(SubsetEvidenceProvider())
    session_id = await _create_session(agent_api_client, user_role="prosecution")
    await _advance(agent_api_client, session_id, 3)
    submitted = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={"action": "submit_evidence", "evidence_ids": ["E01", "E02"]},
    )
    assert submitted.status_code == 200

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "defense",
            "action": "challenge_evidence",
            "evidence_ids": ["E01", "E02"],
            "challenge_dimensions": ["RELEVANCE", "PROBATIVE_VALUE"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace"]["repair_count"] == 0
    assert payload["event"]["payload"]["evidence_ids"] == ["E01"]
    requests = await agent_api_client.get(f"/api/v1/sessions/{session_id}/procedural-requests")
    assert requests.status_code == 200
    assert requests.json()[0]["evidence_ids"] == ["E01"]


async def test_agent_claim_cannot_reverse_earlier_public_position(
    agent_api_client: AsyncClient,
) -> None:
    session_id = await _create_session(agent_api_client, user_role="defense")
    await _advance(agent_api_client, session_id, 3)

    _use_provider(FactPositionProvider("supported_fact"))
    first = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "prosecution",
            "action": "submit_evidence",
            "evidence_ids": ["E04"],
        },
    )
    assert first.status_code == 200

    # 同一立场针对同一事实不视为矛盾，允许继续举证。
    consistent = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "prosecution",
            "action": "submit_evidence",
            "evidence_ids": ["E06"],
        },
    )
    assert consistent.status_code == 200

    _use_provider(FactPositionProvider("disputed_fact"))
    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "prosecution",
            "action": "submit_evidence",
            "evidence_ids": ["E10"],
        },
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "agent_output_forbidden"
    assert "contradicts the role's earlier public position" in (response.json()["error"]["message"])


async def test_agent_context_loads_opposing_public_claims(
    agent_api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # 用户控制辩护方时辩护方无法由 Agent 扮演，因此直接写入双方主张索引做服务层验证。
    session_id = await _create_session(agent_api_client, user_role="defense")
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        record = await unit_of_work.court_sessions.get(session_id)
        assert record is not None
        unit_of_work.court_sessions.add_role_claims(
            session_id,
            event_sequence_number=2,
            phase=CourtPhase.PROSECUTION_EVIDENCE_AND_EXAMINATION.value,
            role="prosecution",
            claims=[
                {
                    "text": "林舟取走相机时具有非法占有目的。",
                    "claim_type": "supported_fact",
                    "fact_ids": ["F13"],
                }
            ],
        )
        unit_of_work.court_sessions.add_role_claims(
            session_id,
            event_sequence_number=3,
            phase=CourtPhase.PROSECUTION_EVIDENCE_AND_EXAMINATION.value,
            role="defense",
            claims=[
                {
                    "text": "聊天记录不能证明林舟实际知悉禁止取用。",
                    "claim_type": "disputed_fact",
                    "fact_ids": ["F04"],
                }
            ],
        )

        context = await build_agent_context(
            unit_of_work,
            session_id,
            record.package_id,
            CourtPhase.PROSECUTION_EVIDENCE_AND_EXAMINATION,
            AgentTurnRequest(
                actor_role=AgentRole.DEFENSE,
                action=CourtAction.CHALLENGE_EVIDENCE,
                evidence_ids=["E04"],
                challenge_dimensions=["RELEVANCE"],
            ),
        )

    assert [claim.text for claim in context.role_public_claims] == [
        "聊天记录不能证明林舟实际知悉禁止取用。"
    ]
    assert len(context.opposing_public_claims) == 1
    opposing = context.opposing_public_claims[0]
    assert opposing.claim_type is ClaimType.SUPPORTED_FACT
    assert opposing.fact_ids == ["F13"]

    # 白名单法源进入上下文，供律师主张法律依据
    sources_by_id = {source.source_id: source for source in context.legal_sources}
    assert "LS-CRIMINAL-LAW-264" in sources_by_id
    assert sources_by_id["LS-CRIMINAL-LAW-264"].category == "substantive"
    assert sources_by_id["LS-CRIMINAL-LAW-264"].article_number == "第二百六十四条"
    # 历史版本条款不在三类白名单中，不下发
    assert "LS-CRIMINAL-LAW-264-2009-HISTORICAL" not in sources_by_id


async def test_agent_legal_citation_must_stay_within_whitelist(
    agent_api_client: AsyncClient,
) -> None:
    session_id = await _create_session(agent_api_client, user_role="defense")
    await _advance(agent_api_client, session_id, 3)

    # 白名单内引用：刑法第二百六十四条（标题省略数据库备注也允许）
    _use_provider(LegalCitationProvider("《中华人民共和国刑法》第二百六十四条"))
    allowed = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "prosecution",
            "action": "submit_evidence",
            "evidence_ids": ["E04"],
        },
    )
    assert allowed.status_code == 200

    # 清单外引用：合同法术条不属于本案法源
    _use_provider(LegalCitationProvider("《中华人民共和国合同法》第一百零七条"))
    rejected = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "prosecution",
            "action": "submit_evidence",
            "evidence_ids": ["E06"],
        },
    )
    assert rejected.status_code == 502
    assert rejected.json()["error"]["code"] == "agent_output_forbidden"
    assert "outside the case legal source whitelist" in rejected.json()["error"]["message"]


async def test_user_can_state_no_objection_and_complete_remaining_evidence_agenda(
    agent_api_client: AsyncClient,
) -> None:
    _use_provider(FakeAgentProvider())
    session_id = await _create_session(agent_api_client, user_role="defense")
    await _advance(agent_api_client, session_id, 3)

    submitted = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "prosecution",
            "action": "submit_evidence",
            "evidence_ids": ["E01", "E02"],
        },
    )
    assert submitted.status_code == 200
    assert "state_no_objection" in submitted.json()["session"]["allowed_actions"]

    no_objection = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={"action": "state_no_objection", "evidence_ids": ["E02"]},
    )
    assert no_objection.status_code == 200
    agenda = await agent_api_client.get(f"/api/v1/sessions/{session_id}/evidence-agenda")
    by_id = {item["evidence_id"]: item for item in agenda.json()}
    assert by_id["E01"]["status"] == "pending"
    assert by_id["E02"]["status"] == "no_objection"

    completed = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/actions", json={"action": "complete_phase"}
    )
    assert completed.status_code == 200
    agenda = await agent_api_client.get(f"/api/v1/sessions/{session_id}/evidence-agenda")
    by_id = {item["evidence_id"]: item for item in agenda.json()}
    assert by_id["E01"]["status"] == "deferred"


async def test_auto_evidence_challenge_runs_in_pending_batches(
    agent_api_client: AsyncClient,
) -> None:
    _use_provider(FakeAgentProvider())
    session_id = await _create_session(agent_api_client, user_role="prosecution")
    await _advance(agent_api_client, session_id, 3)
    submitted = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={"action": "submit_evidence", "evidence_ids": ["E01", "E02", "E03", "E04"]},
    )
    assert submitted.status_code == 200
    completed = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/actions", json={"action": "complete_phase"}
    )
    assert completed.status_code == 200

    first = await agent_api_client.post(f"/api/v1/sessions/{session_id}/auto-step")
    assert first.status_code == 200
    assert first.json()["event"]["action"] == "challenge_evidence"
    assert first.json()["event"]["payload"]["evidence_ids"] == ["E01", "E02", "E03"]

    resolved = await agent_api_client.post(f"/api/v1/sessions/{session_id}/auto-step")
    assert resolved.json()["event"]["action"] == "procedural_request_resolved"
    second = await agent_api_client.post(f"/api/v1/sessions/{session_id}/auto-step")
    assert second.json()["event"]["action"] == "challenge_evidence"
    assert second.json()["event"]["payload"]["evidence_ids"] == ["E04"]

    agenda = await agent_api_client.get(f"/api/v1/sessions/{session_id}/evidence-agenda")
    assert {item["status"] for item in agenda.json()} == {"challenged"}


async def test_agent_anchor_is_materialized_to_authoritative_evidence_quote(
    agent_api_client: AsyncClient,
) -> None:
    provider = AnchoredEvidenceProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 2)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={"actor_role": "defense", "action": "make_statement"},
    )

    assert response.status_code == 200
    payload = response.json()
    citation = payload["output"]["claims"][0]["citations"][0]
    assert provider.calls == 1
    assert payload["trace"]["repair_count"] == 0
    assert payload["trace"]["output_normalized"] is True
    assert citation["evidence_id"] == "E01"
    assert citation["quote"] == (
        "记载序列号QH-X9-042相机及硬箱归青禾影像工作室登记持有；"
        "5月18日18时05分盘点状态为在库，5月19日09时10分状态为缺失。"
    )


async def test_joined_grounded_evidence_notes_are_split_without_model_repair(
    agent_api_client: AsyncClient,
) -> None:
    provider = JoinedEvidenceNotesProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 2)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={"actor_role": "defense", "action": "make_statement"},
    )

    assert response.status_code == 200
    payload = response.json()
    citations = payload["output"]["claims"][0]["citations"]
    assert provider.calls == 1
    assert payload["trace"]["repair_count"] == 0
    assert payload["trace"]["output_normalized"] is True
    assert [item["quote"] for item in citations] == [
        "需核对开启程序、见证人和连续保管情况",
        "手机号下单不能单独排除他人操作",
    ]


async def test_controlled_provider_cannot_fall_back_to_free_text_quote(
    agent_api_client: AsyncClient,
) -> None:
    provider = ControlledLegacyQuoteProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 2)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={"actor_role": "defense", "action": "make_statement"},
    )

    assert response.status_code == 502
    assert provider.calls == 2
    assert response.json()["trace"]["repair_count"] == 1
    assert response.json()["error"] == {
        "code": "agent_output_forbidden",
        "message": "advocate citation must select exactly one valid controlled anchor_id",
    }


async def test_agent_output_cannot_invent_evidence_quote(
    agent_api_client: AsyncClient,
) -> None:
    _use_provider(UnverifiedEvidenceQuoteProvider())
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 2)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={"actor_role": "defense", "action": "make_statement"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "agent_output_forbidden"


async def test_agent_output_cannot_bind_real_evidence_to_unrelated_fact(
    agent_api_client: AsyncClient,
) -> None:
    _use_provider(UnrelatedFactProvider())
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 2)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={"actor_role": "defense", "action": "make_statement"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "agent_output_forbidden"
    assert "not connected" in response.json()["error"]["message"]


async def test_participant_output_cannot_invent_statement_quote(
    agent_api_client: AsyncClient,
) -> None:
    provider = UnverifiedStatementQuoteProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 5)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "witness",
            "participant_id": "W01",
            "action": "make_statement",
        },
    )

    assert response.status_code == 502
    assert provider.calls == 2
    assert response.json()["trace"]["repair_count"] == 1
    assert response.json()["error"]["code"] == "agent_output_forbidden"


async def test_participant_answer_is_rendered_from_valid_citation_without_repair(
    agent_api_client: AsyncClient,
) -> None:
    provider = ParaphrasedParticipantProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 5)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "witness",
            "participant_id": "W01",
            "action": "make_statement",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert provider.calls == 1
    assert payload["trace"]["repair_count"] == 0
    assert payload["trace"]["output_normalized"] is True
    assert payload["output"]["answer"] == payload["output"]["citations"][0]["quote"]
    assert payload["event"]["payload"]["content"] == payload["output"]["answer"]

    traces = await agent_api_client.get(f"/api/v1/sessions/{session_id}/traces")
    assert traces.json()[0]["output_normalized"] is True


async def test_explicit_refusal_with_empty_answer_is_normalized_without_repair(
    agent_api_client: AsyncClient,
) -> None:
    provider = EmptyAnswerRefusalProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 5)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "witness",
            "participant_id": "W01",
            "action": "make_statement",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert provider.calls == 1
    assert payload["trace"]["repair_count"] == 0
    assert payload["trace"]["output_normalized"] is True
    assert payload["output"]["answer"] == payload["output"]["refused_reason"]
    assert payload["event"]["payload"]["content"] == payload["output"]["answer"]


async def test_provider_failure_is_traced_without_courtroom_event(
    agent_api_client: AsyncClient,
) -> None:
    _use_provider(ExplodingProvider())
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 2)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={"actor_role": "defense", "action": "make_statement"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "agent_provider_failed"
    assert response.json()["trace"]["model"] == "provider-error"
    events = await agent_api_client.get(f"/api/v1/sessions/{session_id}/events")
    assert len(events.json()) == 3


async def test_repair_failure_usage_is_recorded_without_blocking_the_next_call(
    agent_api_client: AsyncClient,
) -> None:
    provider = RepairFailureProvider()
    _use_provider(provider)
    app.dependency_overrides[get_settings] = lambda: Settings(session_max_tokens=36)
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 5)

    first = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "witness",
            "participant_id": "W01",
            "action": "make_statement",
        },
    )

    assert first.status_code == 502
    assert first.json()["trace"]["repair_count"] == 1
    assert first.json()["trace"]["input_tokens"] == 30
    assert first.json()["trace"]["output_tokens"] == 6
    assert first.json()["trace"]["estimated_cost_cny"] == pytest.approx(0.03)

    second = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "witness",
            "participant_id": "W01",
            "action": "make_statement",
        },
    )

    assert second.status_code == 502
    assert second.json()["trace"]["input_tokens"] == 20
    assert second.json()["trace"]["output_tokens"] == 4
    assert provider.calls == 3

    usage = await agent_api_client.get(f"/api/v1/sessions/{session_id}/usage")
    assert usage.status_code == 200
    assert usage.json() == {
        "trace_count": 2,
        "input_tokens": 50,
        "output_tokens": 10,
        "total_tokens": 60,
        "latency_ms": usage.json()["latency_ms"],
        "estimated_cost_cny": pytest.approx(0.05),
    }


async def test_user_controlled_role_cannot_be_invoked_as_agent(
    agent_api_client: AsyncClient,
) -> None:
    provider = CapturingFakeProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 2)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={"actor_role": "prosecution", "action": "make_statement"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "user_controlled_role"
    assert provider.requests == []
    traces = await agent_api_client.get(f"/api/v1/sessions/{session_id}/traces")
    assert traces.json() == []


async def test_defendant_agent_uses_only_known_participant_context(
    agent_api_client: AsyncClient,
) -> None:
    provider = CapturingFakeProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 1)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "defendant",
            "participant_id": "D01",
            "action": "make_statement",
        },
    )

    assert response.status_code == 200
    assert response.json()["output"]["kind"] == "defendant"
    context = provider.requests[0].context
    assert context.participant is not None
    assert context.participant.id == "D01"
    assert context.evidence == []
    serialized = context.model_dump_json()
    assert "private_background" not in serialized
    assert "forbidden_fact_ids" not in serialized


async def test_new_defendant_statement_requires_controller_review(
    agent_api_client: AsyncClient,
) -> None:
    _use_provider(NewDefendantStatementProvider())
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 1)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "defendant",
            "participant_id": "D01",
            "action": "make_statement",
        },
    )
    traces = await agent_api_client.get(
        f"/api/v1/sessions/{session_id}/participant-statement-traces"
    )
    trace = traces.json()[0]
    resolution = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/participant-statement-traces/{trace['id']}/resolution",
        json={
            "resolution": "INCLUDED_IN_RECORD",
            "reason": "作为本庭新增陈述保留，但不自动认定相关事实。",
        },
    )
    after = await agent_api_client.get(
        f"/api/v1/sessions/{session_id}/participant-statement-traces"
    )

    assert response.status_code == 200
    assert trace["consistency_status"] == "NEW_STATEMENT_PENDING_REVIEW"
    assert trace["related_fact_ids"] == []
    assert trace["review_status"] is None
    assert resolution.status_code == 200
    assert resolution.json()["resolution"] == "INCLUDED_IN_RECORD"
    assert after.json()[0]["review_status"] == "INCLUDED_IN_RECORD"

    repeated = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/participant-statement-traces/{trace['id']}/resolution",
        json={"resolution": "EXCLUDED_FROM_RECORD", "reason": "重复审核。"},
    )
    other_session_id = await _create_session(agent_api_client, user_role="defense")
    wrong_session = await agent_api_client.post(
        f"/api/v1/sessions/{other_session_id}/participant-statement-traces/{trace['id']}/resolution",
        json={"resolution": "EXCLUDED_FROM_RECORD", "reason": "跨会话审核。"},
    )
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "new_statement_already_reviewed"
    assert wrong_session.status_code == 404


async def test_witness_role_rejects_defendant_participant(
    agent_api_client: AsyncClient,
) -> None:
    provider = CapturingFakeProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 5)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "witness",
            "participant_id": "D01",
            "action": "make_statement",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "participant_role_mismatch"
    assert provider.requests == []


async def test_unknown_session_has_no_agent_traces(agent_api_client: AsyncClient) -> None:
    response = await agent_api_client.get("/api/v1/sessions/missing/traces")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "session_not_found"

    usage = await agent_api_client.get("/api/v1/sessions/missing/usage")
    assert usage.status_code == 404
    assert usage.json()["detail"]["code"] == "session_not_found"


async def test_production_diagnostics_trace_endpoint_requires_key(
    agent_api_client: AsyncClient,
) -> None:
    session_id = await _create_session(agent_api_client)
    settings = Settings(
        app_env="production",
        diagnostics_api_key=SecretStr("d" * 32),
        trace_redaction_hmac_key=SecretStr("h" * 32),
        supabase_url="https://example.supabase.co",
        supabase_jwt_issuer="https://example.supabase.co/auth/v1",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        denied = await agent_api_client.get(f"/api/v1/sessions/{session_id}/traces")
        wrong = await agent_api_client.get(
            f"/api/v1/sessions/{session_id}/traces",
            headers={"X-Diagnostics-Key": "wrong"},
        )
        allowed = await agent_api_client.get(
            f"/api/v1/sessions/{session_id}/traces",
            headers={"X-Diagnostics-Key": "d" * 32},
        )
        usage = await agent_api_client.get(f"/api/v1/sessions/{session_id}/usage")
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert denied.status_code == 401
    assert wrong.status_code == 401
    assert allowed.status_code == 200
    assert usage.status_code == 200


async def test_prosecution_target_context_does_not_receive_defense_position(
    agent_api_client: AsyncClient,
) -> None:
    provider = CapturingFakeProvider()
    _use_provider(provider)
    session_id = await _create_session(agent_api_client, user_role="defense")
    await _advance(agent_api_client, session_id, 5)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={
            "actor_role": "prosecution",
            "action": "question_participant",
            "target_id": "D01",
            "instruction": "请说明当晚为何进入仓库",
        },
    )

    assert response.status_code == 200
    participant = provider.requests[0].context.participant
    assert participant is not None
    assert participant.id == "D01"
    assert participant.defense_position is None
    assert response.json()["output"]["target_id"] == "D01"


async def test_budget_is_observability_only_by_default(
    agent_api_client: AsyncClient,
) -> None:
    _use_provider(ExpensiveProvider())
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 2)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={"actor_role": "defense", "action": "make_statement"},
    )

    assert response.status_code == 200
    assert response.json()["trace"]["estimated_cost_cny"] == 21
    usage = await agent_api_client.get(f"/api/v1/sessions/{session_id}/usage")
    assert usage.json()["total_tokens"] == 1_500
    assert usage.json()["estimated_cost_cny"] == 21


async def test_explicit_budget_enforcement_persists_over_budget_call_as_failed_trace(
    agent_api_client: AsyncClient,
) -> None:
    _use_provider(ExpensiveProvider())
    app.dependency_overrides[get_settings] = lambda: Settings(
        session_budget_enforcement_enabled=True
    )
    session_id = await _create_session(agent_api_client)
    await _advance(agent_api_client, session_id, 2)

    response = await agent_api_client.post(
        f"/api/v1/sessions/{session_id}/agent-turns",
        json={"actor_role": "defense", "action": "make_statement"},
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "session_cost_budget_exceeded"
    events = await agent_api_client.get(f"/api/v1/sessions/{session_id}/events")
    assert len(events.json()) == 3
    traces = await agent_api_client.get(f"/api/v1/sessions/{session_id}/traces")
    assert traces.json()[0]["status"] == "failed"
    assert traces.json()[0]["estimated_cost_cny"] == 21


def test_idle_time_does_not_consume_agent_time_budget() -> None:
    usage = SessionAgentUsage(
        trace_count=1,
        input_tokens=0,
        output_tokens=0,
        latency_ms=0,
        estimated_cost_cny=0,
    )

    # 会话的自然存活时间不再参与预算，恢复旧会话时仍允许继续调用 Agent。
    _validate_budget_before_call(usage, Settings(session_max_seconds=1))


def test_accumulated_agent_latency_exhausts_time_budget() -> None:
    usage = SessionAgentUsage(
        trace_count=1,
        input_tokens=0,
        output_tokens=0,
        latency_ms=1_000,
        estimated_cost_cny=0,
    )

    with pytest.raises(AgentTurnServiceError) as caught:
        _validate_budget_before_call(
            usage,
            Settings(session_budget_enforcement_enabled=True, session_max_seconds=1),
        )

    assert caught.value.code == "session_time_budget_exceeded"
