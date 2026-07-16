from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mootcourt.api.dependencies import get_unit_of_work
from mootcourt.main import app
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.services.case_importer import import_case_package

CASE_PACKAGE = Path(__file__).parents[2] / "data" / "authoring" / "CASE-001"


async def create_session_at_phase(
    client: AsyncClient, role: str, advance_count: int
) -> tuple[str, dict[str, object]]:
    created = await client.post("/api/v1/sessions", json={"case_id": "CASE-001", "user_role": role})
    session_id = created.json()["session_id"]
    state = created.json()
    for _ in range(advance_count):
        advanced = await client.post(
            f"/api/v1/sessions/{session_id}/actions", json={"action": "advance_phase"}
        )
        assert advanced.status_code == 200
        state = advanced.json()["session"]
    return session_id, state


@pytest_asyncio.fixture
async def api_client(
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


async def test_case_view_is_role_scoped(api_client: AsyncClient) -> None:
    cases = await api_client.get("/api/v1/cases")
    prosecution = await api_client.get("/api/v1/cases/CASE-001?role=prosecution")
    defense = await api_client.get("/api/v1/cases/CASE-001?role=defense")

    assert cases.status_code == 200
    assert cases.json()[0]["status"] == "DEVELOPMENT_READY"
    assert prosecution.status_code == 200
    assert defense.status_code == 200
    assert [item["role"] for item in prosecution.json()["role_materials"]] == ["prosecution"]
    assert [item["role"] for item in defense.json()["role_materials"]] == ["defense"]
    serialized = prosecution.text
    assert "AUTHOR_ONLY_NEVER_LOAD_AT_RUNTIME" not in serialized
    assert "private_background" not in serialized
    assert "forbidden_fact_ids" not in serialized


async def test_session_rejects_illegal_action_before_agent_invocation(
    api_client: AsyncClient,
) -> None:
    created = await api_client.post(
        "/api/v1/sessions", json={"case_id": "CASE-001", "user_role": "prosecution"}
    )
    session_id = created.json()["session_id"]

    response = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={"action": "submit_evidence", "evidence_ids": ["E03"]},
    )

    assert created.status_code == 201
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "action_not_allowed"
    state = await api_client.get(f"/api/v1/sessions/{session_id}")
    assert state.json()["turns_used"] == 0
    events = await api_client.get(f"/api/v1/sessions/{session_id}/events")
    assert len(events.json()) == 1


async def test_session_persists_evidence_and_completes_all_phases(
    api_client: AsyncClient,
) -> None:
    created = await api_client.post(
        "/api/v1/sessions", json={"case_id": "CASE-001", "user_role": "prosecution"}
    )
    session_id = created.json()["session_id"]

    state = created.json()
    for _ in range(3):
        advanced = await api_client.post(
            f"/api/v1/sessions/{session_id}/actions", json={"action": "advance_phase"}
        )
        assert advanced.status_code == 200
        state = advanced.json()["session"]
    assert state["phase"] == "PROSECUTION_EVIDENCE_AND_EXAMINATION"

    submitted = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={"action": "submit_evidence", "evidence_ids": ["E03"]},
    )
    assert submitted.status_code == 200
    assert submitted.json()["agent_invoked"] is False
    assert submitted.json()["session"]["submitted_evidence_ids"] == ["E03"]

    for _ in range(8):
        advanced = await api_client.post(
            f"/api/v1/sessions/{session_id}/actions", json={"action": "advance_phase"}
        )
        assert advanced.status_code == 200
        state = advanced.json()["session"]

    assert state["phase"] == "COMPLETED"
    assert state["status"] == "completed"
    persisted = await api_client.get(f"/api/v1/sessions/{session_id}")
    assert persisted.json()["submitted_evidence_ids"] == ["E03"]
    events = await api_client.get(f"/api/v1/sessions/{session_id}/events")
    assert len(events.json()) == 13


async def test_unknown_case_cannot_start_session(api_client: AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/sessions", json={"case_id": "CASE-404", "user_role": "defense"}
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "case_not_found"


async def test_action_cannot_spoof_actor_role(api_client: AsyncClient) -> None:
    session_id, _ = await create_session_at_phase(api_client, "prosecution", 1)

    response = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={"action": "make_statement", "content": "陈述", "role": "controller"},
    )

    assert response.status_code == 422


async def test_statement_requires_content(api_client: AsyncClient) -> None:
    session_id, _ = await create_session_at_phase(api_client, "prosecution", 1)

    response = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions", json={"action": "make_statement"}
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "content_required"


async def test_defense_cannot_submit_during_prosecution_evidence_phase(
    api_client: AsyncClient,
) -> None:
    session_id, _ = await create_session_at_phase(api_client, "defense", 3)

    response = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={"action": "submit_evidence", "evidence_ids": ["E03"]},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "action_not_allowed"


async def test_unsubmitted_evidence_cannot_be_challenged(api_client: AsyncClient) -> None:
    session_id, _ = await create_session_at_phase(api_client, "defense", 3)

    response = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "action": "challenge_evidence",
            "evidence_ids": ["E03"],
            "content": "对真实性提出异议",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "evidence_not_submitted"


async def test_unknown_witness_cannot_be_questioned(api_client: AsyncClient) -> None:
    session_id, state = await create_session_at_phase(api_client, "prosecution", 5)
    assert state["phase"] == "WITNESS_QUESTIONING"

    response = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "action": "question_participant",
            "target_id": "W99",
            "content": "请说明当晚看到的情况",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_participant"


async def test_evidence_cannot_be_submitted_twice(api_client: AsyncClient) -> None:
    session_id, _ = await create_session_at_phase(api_client, "prosecution", 3)
    payload = {"action": "submit_evidence", "evidence_ids": ["E03"]}

    first = await api_client.post(f"/api/v1/sessions/{session_id}/actions", json=payload)
    second = await api_client.post(f"/api/v1/sessions/{session_id}/actions", json=payload)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "evidence_already_submitted"
