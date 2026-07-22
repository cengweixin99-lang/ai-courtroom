from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx

from mootcourt.repositories.deployment import DatabaseRevisionReader
from mootcourt.schemas.delivery_acceptance import (
    DeliveryAcceptanceCheck,
    DeliveryAcceptanceReport,
)

EXPECTED_DATABASE_REVISION = "20260721_0010"
LEGAL_QUERIES = (
    "什么行为是犯罪，情节显著轻微危害不大是否认为是犯罪",
    "明知行为会发生危害社会结果并希望或者放任结果发生是否属于故意犯罪",
    "已满十六周岁的人犯罪是否应当负刑事责任",
    "盗窃罪第二百六十四条的入罪条件",
    "盗窃公私财物数额较大的金额标准",
    "被盗财物有有效价格证明时怎样认定盗窃数额",
)


class DeliveryAcceptanceError(RuntimeError):
    pass


async def run_delivery_acceptance(
    *,
    client: httpx.AsyncClient,
    deployment_repository: DatabaseRevisionReader,
    api_base_url: str,
    web_url: str,
    elasticsearch_url: str,
    case_id: str = "CASE-001",
    full: bool = False,
) -> DeliveryAcceptanceReport:
    checks: list[DeliveryAcceptanceCheck] = []
    artifacts: dict[str, Any] = {}
    package_version: str | None = None
    session_id: str | None = None
    final_phase: str | None = None

    async def check(
        key: str, label: str, operation: Callable[[], Awaitable[str]]
    ) -> bool:
        started = perf_counter()
        try:
            detail = await operation()
            passed = True
        except Exception as exc:
            detail = _safe_failure_detail(exc)
            passed = False
        checks.append(
            DeliveryAcceptanceCheck(
                key=key,
                label=label,
                passed=passed,
                detail=detail,
                latency_ms=(perf_counter() - started) * 1000,
            )
        )
        return passed

    async def api_health() -> str:
        payload = await _json_request(client, "GET", f"{api_base_url}/health")
        if payload.get("status") != "ok":
            raise DeliveryAcceptanceError("API health status is not ok")
        return f"{payload.get('service')} is healthy"

    async def web_health() -> str:
        response = await client.get(web_url)
        response.raise_for_status()
        if "MootCourt" not in response.text and "root" not in response.text:
            raise DeliveryAcceptanceError("web response does not contain the application shell")
        return f"HTTP {response.status_code}"

    async def elasticsearch_health() -> str:
        payload = await _json_request(client, "GET", f"{elasticsearch_url}/_cluster/health")
        status = str(payload.get("status"))
        if status not in {"green", "yellow"}:
            raise DeliveryAcceptanceError(f"Elasticsearch status is {status}")
        return f"cluster status {status}"

    async def database_revision() -> str:
        revision = await deployment_repository.get_database_revision()
        if revision != EXPECTED_DATABASE_REVISION:
            raise DeliveryAcceptanceError(
                "database revision is "
                f"{revision or 'missing'}, expected {EXPECTED_DATABASE_REVISION}"
            )
        return f"database revision {revision}"

    await check("api_health", "API 健康", api_health)
    await check("web_health", "Web 健康", web_health)
    await check("elasticsearch_health", "Elasticsearch 健康", elasticsearch_health)
    await check("database_revision", "数据库迁移", database_revision)

    async def case_and_role_isolation() -> str:
        nonlocal package_version
        cases = await _json_request(client, "GET", f"{api_base_url}/cases")
        selected = next((item for item in cases if item.get("case_id") == case_id), None)
        if selected is None:
            raise DeliveryAcceptanceError(f"case {case_id} is not imported")
        package_version = str(selected["package_version"])
        prosecution = await _json_request(
            client,
            "GET",
            f"{api_base_url}/cases/{case_id}",
            params={"role": "prosecution", "package_version": package_version},
        )
        defense = await _json_request(
            client,
            "GET",
            f"{api_base_url}/cases/{case_id}",
            params={"role": "defense", "package_version": package_version},
        )
        if prosecution.get("role") != "prosecution" or defense.get("role") != "defense":
            raise DeliveryAcceptanceError("role-scoped case response has the wrong role")
        prosecution_roles = {item.get("role") for item in prosecution.get("role_materials", [])}
        defense_roles = {item.get("role") for item in defense.get("role_materials", [])}
        if prosecution_roles - {"prosecution"} or defense_roles - {"defense"}:
            raise DeliveryAcceptanceError("private role materials leaked across courtroom roles")
        artifacts["role_material_counts"] = {
            "prosecution": len(prosecution.get("role_materials", [])),
            "defense": len(defense.get("role_materials", [])),
        }
        return f"{case_id}@{package_version}; role materials are isolated"

    case_ready = await check("case_role_isolation", "案卷与角色隔离", case_and_role_isolation)

    async def legal_index() -> str:
        if package_version is None:
            raise DeliveryAcceptanceError("case package version is unavailable")
        result = await _json_request(
            client,
            "POST",
            f"{api_base_url}/legal/search",
            json={
                "case_id": case_id,
                "package_version": package_version,
                "query": LEGAL_QUERIES[3],
                "top_k": 5,
            },
        )
        if result.get("outcome") != "SUFFICIENT_LEGAL_AUTHORITY" or not result.get("hits"):
            raise DeliveryAcceptanceError("legal index returned insufficient authority")
        artifacts["smoke_legal_trace_id"] = result.get("trace_id")
        return f"{len(result['hits'])} hits; trace {result.get('trace_id')}"

    if case_ready:
        await check("legal_index", "法律索引", legal_index)

    async def session_creation() -> str:
        nonlocal session_id, final_phase
        if package_version is None:
            raise DeliveryAcceptanceError("case package version is unavailable")
        session = await _json_request(
            client,
            "POST",
            f"{api_base_url}/sessions",
            json={
                "case_id": case_id,
                "package_version": package_version,
                "user_role": "defense",
            },
        )
        session_id = str(session["session_id"])
        final_phase = str(session["phase"])
        events = await _json_request(client, "GET", f"{api_base_url}/sessions/{session_id}/events")
        if len(events) != 1 or events[0].get("action") != "session_created":
            raise DeliveryAcceptanceError(
                "new session does not have exactly one audit origin event"
            )
        return f"session {session_id}; initial phase {final_phase}"

    if case_ready:
        await check("session_creation", "庭审会话创建", session_creation)

    if full and session_id is not None:
        async def full_courtroom() -> str:
            nonlocal final_phase
            outcome = await _run_full_courtroom(
                client, api_base_url, case_id, package_version or "", session_id
            )
            final_phase = str(outcome["final_phase"])
            artifacts.update(outcome)
            return f"completed with {outcome['event_count']} events"

        await check("full_courtroom", "真实模型完整庭审", full_courtroom)

    failures = [item.key for item in checks if not item.passed]
    return DeliveryAcceptanceReport(
        mode="full" if full else "smoke",
        generated_at=datetime.now(UTC),
        api_base_url=api_base_url,
        web_url=web_url,
        elasticsearch_url=elasticsearch_url,
        case_id=case_id,
        package_version=package_version,
        session_id=session_id,
        final_phase=final_phase,
        checks=checks,
        failures=failures,
        artifacts=artifacts,
        passed=not failures,
    )


async def _run_full_courtroom(
    client: httpx.AsyncClient,
    api_base_url: str,
    case_id: str,
    package_version: str,
    session_id: str,
) -> dict[str, Any]:
    case_view = await _json_request(
        client,
        "GET",
        f"{api_base_url}/cases/{case_id}",
        params={"role": "defense", "package_version": package_version},
    )
    witnesses = [
        item["id"] for item in case_view.get("participants", [])
        if item.get("participant_type") == "witness"
    ]
    acted_phases: set[str] = set()
    replay_verified = False
    legal_trace_ids: list[str] = []
    auto_step_required = False

    for _ in range(100):
        session = await _json_request(client, "GET", f"{api_base_url}/sessions/{session_id}")
        phase = str(session["phase"])
        allowed = set(session.get("allowed_actions", []))
        if auto_step_required:
            await _run_and_verify_auto_step(
                client,
                api_base_url,
                session_id,
                verify_replay=not replay_verified,
            )
            replay_verified = True
            auto_step_required = False
            continue
        if phase == "LEGAL_ANALYSIS":
            for query in LEGAL_QUERIES:
                result = await _json_request(
                    client,
                    "POST",
                    f"{api_base_url}/legal/search",
                    json={
                        "case_id": case_id,
                        "package_version": package_version,
                        "query": query,
                        "top_k": 5,
                    },
                )
                if result.get("outcome") != "SUFFICIENT_LEGAL_AUTHORITY":
                    raise DeliveryAcceptanceError(
                        f"insufficient legal authority for query: {query}"
                    )
                legal_trace_ids.append(str(result["trace_id"]))
            await _json_request(
                client,
                "POST",
                f"{api_base_url}/sessions/{session_id}/review",
                json={"legal_search_trace_ids": legal_trace_ids},
            )
            # 复盘生成不会直接改变会话阶段，必须交还自动编排推进到 REVIEW。
            auto_step_required = True
            continue
        if phase == "REVIEW":
            review = await _json_request(
                client, "GET", f"{api_base_url}/sessions/{session_id}/review"
            )
            if review.get("turn_diagnostics"):
                await _json_request(
                    client,
                    "POST",
                    f"{api_base_url}/sessions/{session_id}/review/turn-evaluation",
                    json={"event_sequence_numbers": []},
                )
            await _apply_action(client, api_base_url, session_id, {"action": "complete_phase"})
            auto_step_required = True
            continue
        if phase == "COMPLETED":
            events = await _json_request(
                client, "GET", f"{api_base_url}/sessions/{session_id}/events"
            )
            traces = await _json_request(
                client, "GET", f"{api_base_url}/sessions/{session_id}/traces"
            )
            if not replay_verified:
                raise DeliveryAcceptanceError("SSE idempotency replay was not verified")
            return {
                "final_phase": phase,
                "event_count": len(events),
                "agent_trace_count": len(traces),
                "legal_trace_ids": legal_trace_ids,
                "sse_idempotency_replay_verified": True,
            }

        action = await _select_user_action(
            client,
            api_base_url,
            session_id,
            phase,
            allowed,
            acted_phases,
            witnesses,
        )
        if action is not None:
            await _apply_action(client, api_base_url, session_id, action)
            auto_step_required = action["action"] in {
                "complete_phase",
                "question_participant",
            }
            if action["action"] not in {
                "complete_phase",
                "state_no_objection",
                "challenge_evidence",
            }:
                acted_phases.add(phase)
            continue

        await _run_and_verify_auto_step(
            client,
            api_base_url,
            session_id,
            verify_replay=not replay_verified,
        )
        replay_verified = True
    raise DeliveryAcceptanceError("full courtroom exceeded 100 orchestration steps")


async def _run_and_verify_auto_step(
    client: httpx.AsyncClient,
    api_base_url: str,
    session_id: str,
    *,
    verify_replay: bool,
) -> dict[str, Any]:
    idempotency_key = f"acceptance-{uuid4()}"
    first = await _stream_auto_step(client, api_base_url, session_id, idempotency_key)
    if first.get("status") == "failed":
        error = first.get("error") or {}
        raise DeliveryAcceptanceError(
            f"automatic step failed: {error.get('code', 'unknown')}"
        )
    if verify_replay:
        before = await _event_count(client, api_base_url, session_id)
        replay = await _stream_auto_step(client, api_base_url, session_id, idempotency_key)
        after = await _event_count(client, api_base_url, session_id)
        if replay != first or after != before:
            raise DeliveryAcceptanceError("idempotent SSE replay changed the persisted result")
    return first


async def _select_user_action(
    client: httpx.AsyncClient,
    api_base_url: str,
    session_id: str,
    phase: str,
    allowed: set[str],
    acted_phases: set[str],
    witnesses: list[str],
) -> dict[str, Any] | None:
    if "submit_evidence" in allowed:
        statuses = await _json_request(
            client, "GET", f"{api_base_url}/sessions/{session_id}/evidence-statuses"
        )
        evidence_ids = [
            item["evidence_id"] for item in statuses
            if item.get("available_to_current_role") and item.get("status") != "submitted"
        ]
        if evidence_ids:
            return {"action": "submit_evidence", "evidence_ids": evidence_ids}
    if {"challenge_evidence", "state_no_objection"} & allowed:
        agenda = await _json_request(
            client, "GET", f"{api_base_url}/sessions/{session_id}/evidence-agenda"
        )
        pending = [
            item for item in agenda
            if item.get("phase") == phase
            and item.get("responding_role") == "defense"
            and item.get("status") == "pending"
        ]
        if pending:
            evidence_id = pending[0]["evidence_id"]
            if "challenge_evidence" in allowed and phase not in acted_phases:
                return {
                    "action": "challenge_evidence",
                    "evidence_ids": [evidence_id],
                    "challenge_dimensions": ["PROBATIVE_VALUE"],
                    "content": f"辩方对证据{evidence_id}的证明力提出具体质证意见。",
                }
            return {"action": "state_no_objection", "evidence_ids": [evidence_id]}
    if "question_participant" in allowed and phase not in acted_phases and witnesses:
        return {
            "action": "question_participant",
            "target_id": witnesses[0],
            "content": "请说明你亲历的事实，以及能够确认和不能确认的范围。",
        }
    if "make_statement" in allowed and phase not in acted_phases:
        submitted = await _json_request(
            client, "GET", f"{api_base_url}/sessions/{session_id}/evidence-statuses"
        )
        evidence_ids = [
            item["evidence_id"]
            for item in submitted
            if item.get("status") == "submitted"
        ][:1]
        return {
            "action": "make_statement",
            "content": "辩方围绕本庭已经提交的证据说明争议事实与证明力。",
            "evidence_ids": evidence_ids,
        }
    if "complete_phase" in allowed:
        return {"action": "complete_phase"}
    return None


async def _apply_action(
    client: httpx.AsyncClient, api_base_url: str, session_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    result = await _json_request(
        client, "POST", f"{api_base_url}/sessions/{session_id}/actions", json=payload
    )
    if not isinstance(result, dict):
        raise DeliveryAcceptanceError("session action response is not an object")
    return result


async def _event_count(client: httpx.AsyncClient, api_base_url: str, session_id: str) -> int:
    events = await _json_request(client, "GET", f"{api_base_url}/sessions/{session_id}/events")
    return len(events)


async def _stream_auto_step(
    client: httpx.AsyncClient, api_base_url: str, session_id: str, idempotency_key: str
) -> dict[str, Any]:
    completed: dict[str, Any] | None = None
    async with client.stream(
        "POST",
        f"{api_base_url}/sessions/{session_id}/auto-step/stream",
        headers={"Accept": "text/event-stream", "Idempotency-Key": idempotency_key},
    ) as response:
        response.raise_for_status()
        event_name = ""
        data_lines: list[str] = []
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif not line and data_lines:
                payload = json.loads("\n".join(data_lines))
                if event_name == "step.failed":
                    raise DeliveryAcceptanceError(
                        f"SSE step failed: {payload.get('code', 'unknown')}"
                    )
                if event_name == "step.completed":
                    completed = payload
                event_name = ""
                data_lines = []
    if completed is None:
        raise DeliveryAcceptanceError("SSE response ended without step.completed")
    return completed


async def _json_request(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> Any:
    response = await client.request(method, url, **kwargs)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise DeliveryAcceptanceError(
            f"{method} {url} returned {response.status_code}: {response.text[:500]}"
        ) from exc
    return response.json()


def _safe_failure_detail(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").strip()
    return f"{type(exc).__name__}: {text[:800]}"


def delivery_report_markdown(report: DeliveryAcceptanceReport) -> str:
    """生成适合发布记录和人工验收阅读的简洁报告。"""

    rows = [
        "# MootCourt Lab 交付验收报告",
        "",
        f"- 模式：`{report.mode}`",
        f"- 结果：`{'PASS' if report.passed else 'FAIL'}`",
        f"- 生成时间：`{report.generated_at.isoformat()}`",
        f"- 案件：`{report.case_id}@{report.package_version or 'unknown'}`",
        f"- 会话：`{report.session_id or '未创建'}`",
        "",
        "| 检查项 | 结果 | 耗时(ms) | 详情 |",
        "| --- | --- | ---: | --- |",
    ]
    rows.extend(
        f"| {item.label} | {'通过' if item.passed else '失败'} | "
        f"{item.latency_ms:.1f} | {item.detail.replace('|', '\\|')} |"
        for item in report.checks
    )
    rows.extend(["", f"失败项：{', '.join(report.failures) if report.failures else '无'}", ""])
    return "\n".join(rows)
