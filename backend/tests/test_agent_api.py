from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mootcourt.agents.providers import (
    AgentProviderRequest,
    AgentProviderResult,
    FakeAgentProvider,
)
from mootcourt.api.dependencies import get_agent_provider, get_unit_of_work
from mootcourt.core.config import Settings
from mootcourt.main import app
from mootcourt.repositories.agent_traces import SessionAgentUsage
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.services.agent_turns import AgentTurnServiceError, _validate_budget_before_call
from mootcourt.services.case_importer import import_case_package

CASE_PACKAGE = Path(__file__).parents[2] / "data" / "authoring" / "CASE-001"


class CapturingFakeProvider(FakeAgentProvider):
    def __init__(self) -> None:
        self.requests: list[AgentProviderRequest] = []

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        self.requests.append(request)
        return await super().generate(request)


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
                        "evidence_ids": ["E99"],
                    }
                ],
                "requested_action": request.context.action.value,
                "target_id": None,
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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def _create_session(client: AsyncClient, user_role: str = "prosecution") -> str:
    response = await client.post(
        "/api/v1/sessions",
        json={"case_id": "CASE-001", "user_role": user_role},
    )
    assert response.status_code == 201
    return response.json()["session_id"]


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
    # 成功响应返回后，新请求必须立即看到已经提交的证据，不允许短暂回退为未提交。
    statuses = await agent_api_client.get(f"/api/v1/sessions/{session_id}/evidence-statuses")
    by_id = {item["evidence_id"]: item for item in statuses.json()}
    assert by_id["E01"]["status"] == "submitted"


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


async def test_over_budget_agent_call_persists_only_failed_trace(
    agent_api_client: AsyncClient,
) -> None:
    _use_provider(ExpensiveProvider())
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
        input_tokens=0,
        output_tokens=0,
        latency_ms=0,
        estimated_cost_cny=0,
    )

    # 会话的自然存活时间不再参与预算，恢复旧会话时仍允许继续调用 Agent。
    _validate_budget_before_call(usage, Settings(session_max_seconds=1))


def test_accumulated_agent_latency_exhausts_time_budget() -> None:
    usage = SessionAgentUsage(
        input_tokens=0,
        output_tokens=0,
        latency_ms=1_000,
        estimated_cost_cny=0,
    )

    with pytest.raises(AgentTurnServiceError) as caught:
        _validate_budget_before_call(usage, Settings(session_max_seconds=1))

    assert caught.value.code == "session_time_budget_exceeded"
