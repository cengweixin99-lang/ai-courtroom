from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mootcourt.api.dependencies import get_unit_of_work, require_authenticated_principal
from mootcourt.core.auth import AuthenticatedPrincipal
from mootcourt.db.models import (
    CasePackageModel,
    OrganizationMembershipModel,
    OrganizationModel,
    PlatformUserModel,
)
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


async def seed_opposing_evidence(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: str,
    evidence_ids: list[str],
    submitted_by: str,
) -> None:
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        model = await unit_of_work.court_sessions.get(session_id)
        assert model is not None
        responding_role = "defense" if submitted_by == "prosecution" else "prosecution"
        unit_of_work.court_sessions.add_evidence_submissions(session_id, evidence_ids, submitted_by)
        unit_of_work.court_sessions.add_evidence_agenda_items(
            session_id=session_id,
            phase=model.phase,
            evidence_ids=evidence_ids,
            submitted_by=submitted_by,
            responding_role=responding_role,
            submission_event_sequence=None,
        )
        await unit_of_work.commit()


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
    app.dependency_overrides[require_authenticated_principal] = lambda: AuthenticatedPrincipal(
        subject="test-runtime-user",
        email="runtime@example.test",
        provider_role="authenticated",
        claims={"sub": "test-runtime-user"},
    )
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


async def test_session_is_hidden_from_a_different_authenticated_user(
    api_client: AsyncClient,
) -> None:
    created = await api_client.post(
        "/api/v1/sessions", json={"case_id": "CASE-001", "user_role": "defense"}
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    app.dependency_overrides[require_authenticated_principal] = lambda: AuthenticatedPrincipal(
        subject="other-runtime-user",
        email="other@example.test",
        provider_role="authenticated",
        claims={"sub": "other-runtime-user"},
    )
    response = await api_client.get(f"/api/v1/sessions/{session_id}")
    listed = await api_client.get("/api/v1/sessions")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "session_access_denied"
    assert listed.status_code == 200
    assert listed.json() == []


async def test_session_management_is_scoped_to_shared_organizations(
    api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    created = await api_client.post(
        "/api/v1/sessions", json={"case_id": "CASE-001", "user_role": "defense"}
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    async with session_factory() as database_session:
        unit_of_work = SqlAlchemyUnitOfWork(database_session)
        owner = await database_session.scalar(
            select(PlatformUserModel).where(PlatformUserModel.auth_subject == "test-runtime-user")
        )
        assert owner is not None
        instructor = await unit_of_work.identity.get_or_create_user(
            "same-organization-instructor", "instructor@example.test"
        )
        outside_admin = await unit_of_work.identity.get_or_create_user(
            "outside-organization-admin", "outside-admin@example.test"
        )
        shared_organization_id = "11111111-1111-1111-1111-111111111111"
        outside_organization_id = "22222222-2222-2222-2222-222222222222"
        database_session.add_all(
            [
                OrganizationModel(
                    id=shared_organization_id, slug="shared-class", name="Shared Class"
                ),
                OrganizationModel(
                    id=outside_organization_id, slug="outside-class", name="Outside Class"
                ),
                OrganizationMembershipModel(
                    organization_id=shared_organization_id,
                    user_id=owner.id,
                    role="learner",
                ),
                OrganizationMembershipModel(
                    organization_id=shared_organization_id,
                    user_id=instructor.id,
                    role="instructor",
                ),
                OrganizationMembershipModel(
                    organization_id=outside_organization_id,
                    user_id=outside_admin.id,
                    role="admin",
                ),
            ]
        )
        await unit_of_work.commit()

    app.dependency_overrides[require_authenticated_principal] = lambda: AuthenticatedPrincipal(
        subject="same-organization-instructor",
        email="instructor@example.test",
        provider_role="authenticated",
        claims={"sub": "same-organization-instructor"},
    )
    instructor_view = await api_client.get(f"/api/v1/sessions/{session_id}")
    instructor_list = await api_client.get("/api/v1/sessions")

    app.dependency_overrides[require_authenticated_principal] = lambda: AuthenticatedPrincipal(
        subject="outside-organization-admin",
        email="outside-admin@example.test",
        provider_role="authenticated",
        claims={"sub": "outside-organization-admin"},
    )
    outside_view = await api_client.get(f"/api/v1/sessions/{session_id}")
    outside_list = await api_client.get("/api/v1/sessions")

    assert instructor_view.status_code == 200
    assert [item["session_id"] for item in instructor_list.json()] == [session_id]
    assert outside_view.status_code == 403
    assert outside_view.json()["detail"]["code"] == "session_access_denied"
    assert outside_list.status_code == 200
    assert outside_list.json() == []


async def test_session_list_and_archive_preserve_audit_history(api_client: AsyncClient) -> None:
    created = await api_client.post(
        "/api/v1/sessions", json={"case_id": "CASE-001", "user_role": "prosecution"}
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    listed = await api_client.get("/api/v1/sessions")
    archived = await api_client.post(f"/api/v1/sessions/{session_id}/archive")
    replayed = await api_client.post(f"/api/v1/sessions/{session_id}/archive")
    blocked = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions", json={"action": "advance_phase"}
    )
    events = await api_client.get(f"/api/v1/sessions/{session_id}/events")

    assert listed.status_code == 200
    assert [item["session_id"] for item in listed.json()] == [session_id]
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert replayed.status_code == 200
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "session_closed"
    assert [item["action"] for item in events.json()].count("session_archived") == 1


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


async def test_statement_evidence_anchor_must_be_submitted_and_is_persisted(
    api_client: AsyncClient,
) -> None:
    session_id, _ = await create_session_at_phase(api_client, "prosecution", 1)
    rejected = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={"action": "make_statement", "content": "公诉意见", "evidence_ids": ["E03"]},
    )

    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "statement_evidence_not_submitted"

    for _ in range(2):
        await api_client.post(
            f"/api/v1/sessions/{session_id}/actions", json={"action": "advance_phase"}
        )
    submitted = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={"action": "submit_evidence", "evidence_ids": ["E03"]},
    )
    assert submitted.status_code == 200
    for _ in range(3):
        await api_client.post(
            f"/api/v1/sessions/{session_id}/actions", json={"action": "advance_phase"}
        )

    accepted = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "action": "make_statement",
            "content": "结合证据发表公诉意见。",
            "evidence_ids": ["E03"],
        },
    )

    assert accepted.status_code == 200
    assert accepted.json()["event"]["payload"]["evidence_ids"] == ["E03"]


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


async def test_evidence_status_ledger_tracks_submission(api_client: AsyncClient) -> None:
    session_id, _ = await create_session_at_phase(api_client, "prosecution", 3)

    before = await api_client.get(f"/api/v1/sessions/{session_id}/evidence-statuses")
    submitted = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={"action": "submit_evidence", "evidence_ids": ["E03"]},
    )
    after = await api_client.get(f"/api/v1/sessions/{session_id}/evidence-statuses")

    assert before.status_code == 200
    assert submitted.status_code == 200
    before_by_id = {item["evidence_id"]: item for item in before.json()}
    after_by_id = {item["evidence_id"]: item for item in after.json()}
    assert before_by_id["E03"]["status"] == "not_submitted"
    assert after_by_id["E03"]["status"] == "submitted"
    assert after_by_id["E03"]["submitted_by"] == "prosecution"
    assert after_by_id["E03"]["submitted_at"]


async def test_structured_evidence_challenge_is_recorded(
    api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id, _ = await create_session_at_phase(api_client, "defense", 3)
    await seed_opposing_evidence(session_factory, session_id, ["E03"], "prosecution")

    response = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "action": "challenge_evidence",
            "evidence_ids": ["E03"],
            "challenge_dimensions": ["AUTHENTICITY", "PROBATIVE_VALUE"],
            "content": "门禁卡被使用不能单独证明持卡人本人进入。",
        },
    )
    requests = await api_client.get(f"/api/v1/sessions/{session_id}/procedural-requests")

    assert response.status_code == 200
    assert requests.status_code == 200
    assert requests.json()[0]["request_type"] == "EVIDENCE_CHALLENGE"
    assert requests.json()[0]["challenge_dimensions"] == [
        "AUTHENTICITY",
        "PROBATIVE_VALUE",
    ]
    assert requests.json()[0]["status"] == "recorded_for_evaluation"
    assert response.json()["event"]["payload"]["procedural_request_id"]


async def test_evidence_challenge_requires_dimension_after_submission_check(
    api_client: AsyncClient,
) -> None:
    session_id, _ = await create_session_at_phase(api_client, "defense", 3)

    unsubmitted = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "action": "challenge_evidence",
            "evidence_ids": ["E03"],
            "content": "提出质证意见",
        },
    )

    assert unsubmitted.status_code == 409
    assert unsubmitted.json()["detail"]["code"] == "evidence_not_submitted"


async def test_question_control_requests_are_structured_and_publicly_recorded(
    api_client: AsyncClient,
) -> None:
    session_id, _ = await create_session_at_phase(api_client, "prosecution", 5)
    first_question = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "action": "question_participant",
            "target_id": "W01",
            "content": "你在仓库门口看到了谁？",
        },
    )
    target_sequence = first_question.json()["event"]["sequence_number"]

    for request_type in ["IRRELEVANT_QUESTION", "IMPROPER_QUESTION"]:
        response = await api_client.post(
            f"/api/v1/sessions/{session_id}/actions",
            json={
                "action": "raise_procedural_request",
                "procedural_request_type": request_type,
                "target_event_sequence": target_sequence,
                "content": f"请求按{request_type}处理该问题。",
            },
        )
        assert response.status_code == 200
        assert response.json()["event"]["payload"]["procedural_request_type"] == request_type

    requests = await api_client.get(f"/api/v1/sessions/{session_id}/procedural-requests")
    events = await api_client.get(f"/api/v1/sessions/{session_id}/events")

    assert [item["request_type"] for item in requests.json()] == [
        "IRRELEVANT_QUESTION",
        "IMPROPER_QUESTION",
    ]
    assert all(item["status"] == "pending_controller_review" for item in requests.json())
    assert events.json()[-1]["payload"]["procedural_request_id"] == requests.json()[-1]["id"]


async def test_repetitive_question_request_requires_actual_duplicate(
    api_client: AsyncClient,
) -> None:
    session_id, _ = await create_session_at_phase(api_client, "defense", 5)
    first = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "action": "question_participant",
            "target_id": "W01",
            "content": "你是否看清了离开人员的面部？",
        },
    )
    not_repetitive = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "action": "raise_procedural_request",
            "procedural_request_type": "REPETITIVE_QUESTION",
            "target_event_sequence": first.json()["event"]["sequence_number"],
            "content": "请求制止重复发问。",
        },
    )
    second = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "action": "question_participant",
            "target_id": "W01",
            "content": " 你是否看清了离开人员的面部? ",
        },
    )
    repetitive = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "action": "raise_procedural_request",
            "procedural_request_type": "REPETITIVE_QUESTION",
            "target_event_sequence": second.json()["event"]["sequence_number"],
            "content": "该问题此前已经提出，请求制止。",
        },
    )

    assert not_repetitive.status_code == 422
    assert not_repetitive.json()["detail"]["code"] == "question_not_repetitive"
    assert repetitive.status_code == 200
    assert repetitive.json()["event"]["payload"]["procedural_request_status"] == (
        "pending_controller_review"
    )


async def test_procedural_request_must_target_question_event(api_client: AsyncClient) -> None:
    session_id, _ = await create_session_at_phase(api_client, "prosecution", 5)

    response = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "action": "raise_procedural_request",
            "procedural_request_type": "IMPROPER_QUESTION",
            "target_event_sequence": 1,
            "content": "请求制止不当发问。",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "target_event_not_question"


async def test_controller_resolves_question_request_and_publishes_event(
    api_client: AsyncClient,
) -> None:
    session_id, _ = await create_session_at_phase(api_client, "prosecution", 5)
    question = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "action": "question_participant",
            "target_id": "W01",
            "content": "你是否亲眼看清离开人员的面部？",
        },
    )
    raised = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "action": "raise_procedural_request",
            "procedural_request_type": "IMPROPER_QUESTION",
            "target_event_sequence": question.json()["event"]["sequence_number"],
            "content": "该问题含有不当诱导，请求制止。",
        },
    )
    request_id = raised.json()["event"]["payload"]["procedural_request_id"]

    resolved = await api_client.post(
        f"/api/v1/sessions/{session_id}/procedural-requests/{request_id}/resolution",
        json={"resolution": "APPROVED", "reason": "问题含有预设事实，应当调整问法。"},
    )
    repeated = await api_client.post(
        f"/api/v1/sessions/{session_id}/procedural-requests/{request_id}/resolution",
        json={"resolution": "REJECTED", "reason": "尝试重复处理。"},
    )

    assert resolved.status_code == 200
    body = resolved.json()
    assert body["request"]["status"] == "resolved"
    assert body["request"]["resolution"] == "APPROVED"
    assert body["event"]["action"] == "procedural_request_resolved"
    assert body["event"]["actor_role"] == "controller"
    assert body["request"]["resolution_event_sequence"] == body["event"]["sequence_number"]
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "procedural_request_already_resolved"


async def test_resolution_is_session_scoped_and_matches_request_kind(
    api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id, _ = await create_session_at_phase(api_client, "defense", 3)
    await seed_opposing_evidence(session_factory, session_id, ["E03"], "prosecution")
    challenge = await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "action": "challenge_evidence",
            "evidence_ids": ["E03"],
            "challenge_dimensions": ["AUTHENTICITY"],
            "content": "对证据真实性提出质证意见。",
        },
    )
    request_id = challenge.json()["event"]["payload"]["procedural_request_id"]
    other_session_id, _ = await create_session_at_phase(api_client, "defense", 1)

    wrong_session = await api_client.post(
        f"/api/v1/sessions/{other_session_id}/procedural-requests/{request_id}/resolution",
        json={"resolution": "RECORDED", "reason": "记入评议。"},
    )
    wrong_resolution = await api_client.post(
        f"/api/v1/sessions/{session_id}/procedural-requests/{request_id}/resolution",
        json={"resolution": "APPROVED", "reason": "错误的处理类型。"},
    )
    recorded = await api_client.post(
        f"/api/v1/sessions/{session_id}/procedural-requests/{request_id}/resolution",
        json={"resolution": "RECORDED", "reason": "质证意见记入最终评议材料。"},
    )

    assert wrong_session.status_code == 404
    assert wrong_resolution.status_code == 422
    assert wrong_resolution.json()["detail"]["code"] == "procedural_resolution_mismatch"
    assert recorded.status_code == 200
    assert recorded.json()["request"]["resolution"] == "RECORDED"


async def test_evidence_fact_summary_reports_usage_without_fact_finding(
    api_client: AsyncClient,
) -> None:
    session_id, _ = await create_session_at_phase(api_client, "prosecution", 3)
    before = await api_client.get(f"/api/v1/sessions/{session_id}/evidence-fact-summary")
    await api_client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={"action": "submit_evidence", "evidence_ids": ["E03"]},
    )
    after = await api_client.get(f"/api/v1/sessions/{session_id}/evidence-fact-summary")

    assert before.status_code == 200
    assert after.status_code == 200
    before_by_id = {item["fact_id"]: item for item in before.json()}
    after_by_id = {item["fact_id"]: item for item in after.json()}
    assert before_by_id["F03"]["support_status"] == "NO_SUBMITTED_SUPPORT"
    assert after_by_id["F03"]["submitted_evidence_ids"] == ["E03"]
    assert after_by_id["F03"]["support_status"] == "SUPPORTED_BY_SUBMITTED_EVIDENCE"
    assert after_by_id["F03"]["fact_record_status"] == "supported"


async def test_structured_review_requires_verified_case_legal_traces(
    api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id, state = await create_session_at_phase(api_client, "prosecution", 9)
    assert state["phase"] == "LEGAL_ANALYSIS"

    missing = await api_client.post(
        f"/api/v1/sessions/{session_id}/review",
        json={"legal_search_trace_ids": ["missing"]},
    )

    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "legal_trace_not_found"

    async with session_factory() as database_session:
        unit_of_work = SqlAlchemyUnitOfWork(database_session)
        package = await database_session.scalar(
            select(CasePackageModel).where(CasePackageModel.case_id == "CASE-001")
        )
        assert package is not None
        runtime_package = await unit_of_work.case_packages.get_runtime_package_by_database_id(
            package.id
        )
        assert runtime_package is not None
        hits = []
        for index, source in enumerate(runtime_package.legal_sources, start=1):
            payload = source.payload
            if source.source_id == "LS-CRIMINAL-LAW-264-2009-HISTORICAL":
                continue
            hits.append(
                {
                    "source_id": source.source_id,
                    "instrument_title": source.instrument_title,
                    "article_number": source.article_number,
                    "text": payload["text_snapshot"],
                    "jurisdiction": payload["jurisdiction"],
                    "effective_from": payload.get("effective_from"),
                    "effective_to": payload.get("effective_to"),
                    "status": payload["status"],
                    "review_status": payload["review_status"],
                    "authority_level": payload["authority_level"],
                    "official_source_url": payload.get("official_source_url"),
                    "version_hash": payload.get("version_hash"),
                    "score": float(20 - index),
                    "retrieval_mode": "bm25",
                    "bm25_score": float(20 - index),
                    "vector_score": None,
                    "bm25_rank": index,
                    "vector_rank": None,
                }
            )
        trace = await unit_of_work.legal_search_traces.add(
            package_id=package.id,
            legal_profile_id=str(package.legal_profile["id"]),
            query="本案全部冻结构成要件对应法源",
            retrieval_mode="bm25",
            embedding_version=None,
            outcome="SUFFICIENT_LEGAL_AUTHORITY",
            filters={
                "jurisdiction": "PRC",
                "law_as_of_date": "2026-07-14",
                "allowed_source_ids": [item["source_id"] for item in hits],
                "approved_review_statuses": ["verified"],
                "top_k": 20,
            },
            hits=hits,
            latency_ms=1,
        )
        await unit_of_work.commit()
        trace_id = trace.id

    created = await api_client.post(
        f"/api/v1/sessions/{session_id}/review",
        json={"legal_search_trace_ids": [trace_id]},
    )
    fetched = await api_client.get(f"/api/v1/sessions/{session_id}/review")
    repeated = await api_client.post(
        f"/api/v1/sessions/{session_id}/review",
        json={"legal_search_trace_ids": [trace_id]},
    )

    assert created.status_code == 200
    body = created.json()
    assert len(body["element_findings"]) == 6
    assert all(item["citations"] for item in body["element_findings"])
    assert 0 <= body["total_score"] <= 100
    assert {item["key"] for item in body["score_dimensions"]} == {
        "priority_evidence_submission",
        "opponent_evidence_response",
        "legal_authority_coverage",
        "issue_closure",
    }
    assert any(item["id"] == "missing-priority-evidence" for item in body["recommendations"])
    assert body["deterministic_conclusion_allowed"] is False
    assert body["conclusion"] is None
    assert "教学模拟" in body["disclaimer"]
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "court_review_already_exists"
