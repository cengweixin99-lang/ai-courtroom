import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from mootcourt.cli import accept_delivery as delivery_cli
from mootcourt.repositories.deployment import DeploymentRepository
from mootcourt.schemas.delivery_acceptance import DeliveryAcceptanceCheck, DeliveryAcceptanceReport
from mootcourt.services import delivery_acceptance as acceptance
from mootcourt.services.delivery_acceptance import (
    EXPECTED_DATABASE_REVISION,
    _run_and_verify_auto_step,
    _run_full_courtroom,
    _select_user_action,
    _stream_auto_step,
    delivery_report_markdown,
    run_delivery_acceptance,
)


class StubDeploymentRepository:
    def __init__(self, revision: str | None = EXPECTED_DATABASE_REVISION) -> None:
        self.revision = revision

    async def get_database_revision(self) -> str | None:
        return self.revision


def _case_view(role: str) -> dict[str, Any]:
    return {
        "case_id": "CASE-001",
        "package_version": "0.2.0-dev",
        "role": role,
        "role_materials": [{"id": f"RM-{role}", "role": role}],
    }


async def test_delivery_smoke_acceptance_uses_public_runtime_boundaries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/health":
            return httpx.Response(200, json={"status": "ok", "service": "mootcourt-api"})
        if path == "/":
            return httpx.Response(200, text='<div id="root">MootCourt Lab</div>')
        if path == "/_cluster/health":
            return httpx.Response(200, json={"status": "green"})
        if path == "/api/v1/cases":
            return httpx.Response(
                200,
                json=[{"case_id": "CASE-001", "package_version": "0.2.0-dev"}],
            )
        if path == "/api/v1/cases/CASE-001":
            return httpx.Response(200, json=_case_view(request.url.params["role"]))
        if path == "/api/v1/legal/search":
            return httpx.Response(
                200,
                json={
                    "outcome": "SUFFICIENT_LEGAL_AUTHORITY",
                    "hits": [{"source_id": "LAW-001"}],
                    "trace_id": "trace-001",
                },
            )
        if path == "/api/v1/sessions" and request.method == "POST":
            return httpx.Response(
                201,
                json={"session_id": "session-001", "phase": "COURT_OPENING"},
            )
        if path == "/api/v1/sessions/session-001/events":
            return httpx.Response(200, json=[{"action": "session_created"}])
        raise AssertionError(f"unexpected request: {request.method} {path}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_delivery_acceptance(
            client=client,
            deployment_repository=StubDeploymentRepository(),
            api_base_url="http://api.test/api/v1",
            web_url="http://web.test/",
            elasticsearch_url="http://es.test",
        )

    assert report.passed
    assert report.mode == "smoke"
    assert report.package_version == "0.2.0-dev"
    assert report.session_id == "session-001"
    assert report.artifacts["smoke_legal_trace_id"] == "trace-001"
    assert {item.key for item in report.checks} == {
        "api_health",
        "web_health",
        "elasticsearch_health",
        "database_revision",
        "case_role_isolation",
        "legal_index",
        "session_creation",
    }


async def test_delivery_smoke_reports_database_and_role_isolation_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/health":
            return httpx.Response(200, json={"status": "ok", "service": "mootcourt-api"})
        if path == "/":
            return httpx.Response(200, text='<div id="root"></div>')
        if path == "/_cluster/health":
            return httpx.Response(200, json={"status": "yellow"})
        if path == "/api/v1/cases":
            return httpx.Response(
                200,
                json=[{"case_id": "CASE-001", "package_version": "0.2.0-dev"}],
            )
        if path == "/api/v1/cases/CASE-001":
            payload = _case_view(request.url.params["role"])
            payload["role_materials"] = [{"id": "LEAK", "role": "prosecution"}]
            return httpx.Response(200, json=payload)
        raise AssertionError(f"unexpected request: {request.method} {path}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_delivery_acceptance(
            client=client,
            deployment_repository=StubDeploymentRepository("old-revision"),
            api_base_url="http://api.test/api/v1",
            web_url="http://web.test/",
            elasticsearch_url="http://es.test",
        )

    assert not report.passed
    assert report.failures == ["database_revision", "case_role_isolation"]
    assert report.session_id is None
    assert "expected 20260721_0010" in next(
        item.detail for item in report.checks if item.key == "database_revision"
    )


async def test_stream_auto_step_requires_completed_event_and_preserves_payload() -> None:
    sse = (
        'event: step.started\ndata: {"session_id":"session-001"}\n\n'
        'event: turn.delta\ndata: {"text":"正在生成"}\n\n'
        'event: step.completed\ndata: {"status":"waiting_for_user","session":{}}\n\n'
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["idempotency-key"] == "key-001"
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        payload = await _stream_auto_step(
            client, "http://api.test/api/v1", "session-001", "key-001"
        )

    assert payload["status"] == "waiting_for_user"


async def test_full_courtroom_generates_review_evaluation_and_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = iter(
        [
            {"phase": "COURT_OPENING", "allowed_actions": []},
            {"phase": "LEGAL_ANALYSIS", "allowed_actions": []},
            {"phase": "REVIEW", "allowed_actions": ["complete_phase"]},
            {"phase": "REVIEW", "allowed_actions": ["complete_phase"]},
            {"phase": "COMPLETED", "allowed_actions": []},
            {"phase": "COMPLETED", "allowed_actions": []},
        ]
    )
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def json_request(
        _client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
    ) -> Any:
        calls.append((method, url, kwargs))
        if url.endswith("/cases/CASE-001"):
            return {"participants": []}
        if url.endswith("/sessions/session-001"):
            return next(sessions)
        if url.endswith("/legal/search"):
            search_count = len([item for item in calls if item[1].endswith("/legal/search")])
            return {
                "outcome": "SUFFICIENT_LEGAL_AUTHORITY",
                "trace_id": f"trace-{search_count}",
            }
        if url.endswith("/review") and method == "GET":
            return {"turn_diagnostics": [{"event_sequence_number": 3}]}
        if url.endswith("/events"):
            return [{"action": "session_created"}, {"action": "advance_phase"}]
        if url.endswith("/traces"):
            return [{"trace_id": "agent-trace-001"}]
        return {}

    auto_step = AsyncMock(return_value={"status": "progressed"})
    apply_action = AsyncMock(return_value={})
    monkeypatch.setattr(acceptance, "_json_request", json_request)
    monkeypatch.setattr(acceptance, "_run_and_verify_auto_step", auto_step)
    monkeypatch.setattr(acceptance, "_apply_action", apply_action)
    monkeypatch.setattr(acceptance, "_select_user_action", AsyncMock(return_value=None))

    async with httpx.AsyncClient() as client:
        result = await _run_full_courtroom(
            client, "http://api.test/api/v1", "CASE-001", "0.2.0-dev", "session-001"
        )

    assert result["final_phase"] == "COMPLETED"
    assert result["event_count"] == 2
    assert result["agent_trace_count"] == 1
    assert len(result["legal_trace_ids"]) == 6
    assert result["sse_idempotency_replay_verified"] is True
    assert auto_step.await_count == 3
    assert auto_step.await_args_list[0].kwargs["verify_replay"] is True
    assert auto_step.await_args_list[1].kwargs["verify_replay"] is False
    assert auto_step.await_args_list[2].kwargs["verify_replay"] is False
    assert any(url.endswith("/review/turn-evaluation") for _, url, _ in calls)
    apply_action.assert_awaited_once_with(
        client,
        "http://api.test/api/v1",
        "session-001",
        {"action": "complete_phase"},
    )


async def test_select_user_action_covers_evidence_question_statement_and_completion() -> None:
    responses: dict[str, list[dict[str, Any]]] = {
        "evidence-statuses": [
            {
                "evidence_id": "E01",
                "available_to_current_role": True,
                "status": "not_submitted",
            }
        ],
        "evidence-agenda": [
            {
                "evidence_id": "E02",
                "phase": "PROSECUTION_EVIDENCE_AND_EXAMINATION",
                "responding_role": "defense",
                "status": "pending",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", maxsplit=1)[-1]
        return httpx.Response(200, json=responses[name])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        submit = await _select_user_action(
            client,
            "http://api.test/api/v1",
            "session-001",
            "DEFENSE_EVIDENCE_AND_EXAMINATION",
            {"submit_evidence"},
            set(),
            [],
        )
        challenge = await _select_user_action(
            client,
            "http://api.test/api/v1",
            "session-001",
            "PROSECUTION_EVIDENCE_AND_EXAMINATION",
            {"challenge_evidence", "state_no_objection"},
            set(),
            [],
        )
        no_objection = await _select_user_action(
            client,
            "http://api.test/api/v1",
            "session-001",
            "PROSECUTION_EVIDENCE_AND_EXAMINATION",
            {"challenge_evidence", "state_no_objection"},
            {"PROSECUTION_EVIDENCE_AND_EXAMINATION"},
            [],
        )
        question = await _select_user_action(
            client,
            "http://api.test/api/v1",
            "session-001",
            "WITNESS_QUESTIONING",
            {"question_participant"},
            set(),
            ["W01"],
        )
        complete = await _select_user_action(
            client,
            "http://api.test/api/v1",
            "session-001",
            "COURT_INVESTIGATION",
            {"complete_phase"},
            set(),
            [],
        )

    assert submit == {"action": "submit_evidence", "evidence_ids": ["E01"]}
    assert challenge is not None and challenge["action"] == "challenge_evidence"
    assert no_objection == {"action": "state_no_objection", "evidence_ids": ["E02"]}
    assert question is not None and question["target_id"] == "W01"
    assert complete == {"action": "complete_phase"}


async def test_auto_step_replay_does_not_add_an_event(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = AsyncMock(return_value={"status": "progressed", "session": {}})
    counts = iter([3, 3])
    monkeypatch.setattr(acceptance, "_stream_auto_step", stream)
    monkeypatch.setattr(acceptance, "_event_count", AsyncMock(side_effect=lambda *_: next(counts)))

    async with httpx.AsyncClient() as client:
        result = await _run_and_verify_auto_step(
            client,
            "http://api.test/api/v1",
            "session-001",
            verify_replay=True,
        )

    assert result["status"] == "progressed"
    assert stream.await_count == 2


def test_delivery_markdown_is_human_readable() -> None:
    report = DeliveryAcceptanceReport(
        mode="smoke",
        generated_at=datetime(2026, 7, 22, tzinfo=UTC),
        api_base_url="http://localhost:8000/api/v1",
        web_url="http://localhost:5173",
        elasticsearch_url="http://localhost:9200",
        case_id="CASE-001",
        package_version="0.2.0-dev",
        session_id="session-001",
        checks=[
            DeliveryAcceptanceCheck(
                key="api_health",
                label="API 健康",
                passed=True,
                detail="HTTP 200 | ok",
                latency_ms=12.3,
            )
        ],
        failures=[],
        passed=True,
    )

    markdown = delivery_report_markdown(report)

    assert "结果：`PASS`" in markdown
    assert "| API 健康 | 通过 | 12.3 | HTTP 200 \\| ok |" in markdown


async def test_deployment_repository_reads_current_alembic_revision() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
            await connection.execute(
                text("INSERT INTO alembic_version VALUES (:revision)"),
                {"revision": EXPECTED_DATABASE_REVISION},
            )

        repository = DeploymentRepository(engine)

        assert await repository.get_database_revision() == EXPECTED_DATABASE_REVISION
    finally:
        await engine.dispose()


async def test_delivery_acceptance_full_mode_records_failed_orchestration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/health":
            return httpx.Response(200, json={"status": "ok", "service": "mootcourt-api"})
        if path == "/":
            return httpx.Response(200, text='<div id="root"></div>')
        if path == "/_cluster/health":
            return httpx.Response(200, json={"status": "green"})
        if path == "/api/v1/cases":
            return httpx.Response(
                200,
                json=[{"case_id": "CASE-001", "package_version": "0.2.0-dev"}],
            )
        if path == "/api/v1/cases/CASE-001":
            return httpx.Response(200, json=_case_view(request.url.params["role"]))
        if path == "/api/v1/legal/search":
            return httpx.Response(
                200,
                json={
                    "outcome": "SUFFICIENT_LEGAL_AUTHORITY",
                    "hits": [{"source_id": "LAW-001"}],
                    "trace_id": "trace-001",
                },
            )
        if path == "/api/v1/sessions" and request.method == "POST":
            return httpx.Response(
                201,
                json={"session_id": "session-001", "phase": "COURT_OPENING"},
            )
        if path == "/api/v1/sessions/session-001/events":
            return httpx.Response(200, json=[{"action": "session_created"}])
        raise AssertionError(f"unexpected request: {request.method} {path}")

    monkeypatch.setattr(
        acceptance,
        "_run_full_courtroom",
        AsyncMock(side_effect=acceptance.DeliveryAcceptanceError("provider unavailable")),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_delivery_acceptance(
            client=client,
            deployment_repository=StubDeploymentRepository(),
            api_base_url="http://api.test/api/v1",
            web_url="http://web.test/",
            elasticsearch_url="http://es.test",
            full=True,
        )

    assert not report.passed
    assert report.mode == "full"
    assert report.failures == ["full_courtroom"]
    assert "provider unavailable" in report.checks[-1].detail


async def test_delivery_cli_writes_json_and_markdown_reports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = DeliveryAcceptanceReport(
        mode="smoke",
        generated_at=datetime(2026, 7, 22, tzinfo=UTC),
        api_base_url="http://api.test/api/v1",
        web_url="http://web.test",
        elasticsearch_url="http://es.test",
        case_id="CASE-001",
        package_version="0.2.0-dev",
        checks=[],
        failures=[],
        passed=True,
    )

    class FakeEngine:
        async def dispose(self) -> None:
            return None

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(delivery_cli, "create_async_engine", lambda _url: FakeEngine())
    monkeypatch.setattr(
        "mootcourt.cli.accept_delivery.httpx.AsyncClient",
        lambda **_kwargs: FakeClient(),
    )
    runner = AsyncMock(return_value=report)
    monkeypatch.setattr(delivery_cli, "run_delivery_acceptance", runner)
    output = tmp_path / "delivery.json"
    markdown = tmp_path / "delivery-report.md"
    args = argparse.Namespace(
        database_url="sqlite+aiosqlite:///:memory:",
        timeout=10,
        api_base_url="http://api.test/api/v1/",
        web_url="http://web.test/",
        elasticsearch_url="http://es.test/",
        case_id="CASE-001",
        full=False,
        output=output,
        markdown_output=markdown,
    )

    passed = await delivery_cli._run(args)

    assert passed
    assert '"passed": true' in output.read_text(encoding="utf-8")
    assert "结果：`PASS`" in markdown.read_text(encoding="utf-8")
    assert runner.await_args is not None
    assert runner.await_args.kwargs["api_base_url"] == "http://api.test/api/v1"


def test_delivery_cli_uses_a_unique_report_name_for_full_acceptance() -> None:
    generated_at = datetime(2026, 7, 23, 3, 30, 45, tzinfo=UTC)

    assert delivery_cli._default_output_path(full=False) == Path(
        "../evals/delivery/results/smoke.json"
    )
    assert delivery_cli._default_output_path(full=True, generated_at=generated_at) == Path(
        "../evals/delivery/results/full_20260723T033045Z.json"
    )


def test_delivery_cli_rejects_stale_expected_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["mootcourt-accept-delivery"])
    script = type("Script", (), {"get_current_head": lambda self: "new-head"})()
    monkeypatch.setattr(
        "mootcourt.cli.accept_delivery.ScriptDirectory.from_config",
        lambda _config: script,
    )

    with pytest.raises(SystemExit, match="Alembic head is new-head"):
        delivery_cli.main()


def test_delivery_cli_exits_nonzero_when_report_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["mootcourt-accept-delivery"])
    script = type(
        "Script",
        (),
        {"get_current_head": lambda self: EXPECTED_DATABASE_REVISION},
    )()
    monkeypatch.setattr(
        "mootcourt.cli.accept_delivery.ScriptDirectory.from_config",
        lambda _config: script,
    )

    def reject_report(coroutine: Any) -> bool:
        coroutine.close()
        return False

    monkeypatch.setattr("mootcourt.cli.accept_delivery.asyncio.run", reject_report)

    with pytest.raises(SystemExit) as exc_info:
        delivery_cli.main()

    assert exc_info.value.code == 1
