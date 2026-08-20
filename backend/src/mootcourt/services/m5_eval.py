from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mootcourt.agents.providers import (
    AgentProviderRequest,
    AgentProviderResult,
    FakeAgentProvider,
)
from mootcourt.core.config import Settings
from mootcourt.domain.courtroom import CourtAction, CourtPhase
from mootcourt.repositories.legal_search import LegalSearchRepository
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.agents import AgentRole, AgentTurnRequest
from mootcourt.schemas.eval.legal_eval import LegalEvalDataset, load_legal_eval_dataset
from mootcourt.schemas.eval.m5_eval import (
    EndToEndEvalCase,
    EvalCaseResult,
    EvalMetricCheck,
    LoadedM5Datasets,
    M5CostMetrics,
    M5EvalReport,
    M5Subset,
    ParticipantEvalCase,
    ProcedureEvalCase,
)
from mootcourt.schemas.reviews import CourtReviewGenerateRequest
from mootcourt.schemas.runtime import SessionActionRequest, SessionCreate, UserRole
from mootcourt.search.embeddings import EmbeddingProvider
from mootcourt.services.agent_turns import AgentTurnServiceError, execute_agent_turn
from mootcourt.services.court_reviews import generate_court_review
from mootcourt.services.court_sessions import (
    SessionServiceError,
    apply_session_action,
    create_court_session,
    get_session_view,
)
from mootcourt.services.legal_eval import evaluate_legal_retrieval


class _EvalProvider:
    def __init__(self, output: dict[str, Any] | None) -> None:
        self._output = output
        self._fallback = FakeAgentProvider()
        self.requests: list[AgentProviderRequest] = []

    @property
    def provider_name(self) -> str:
        return "m5-eval"

    @property
    def model_name(self) -> str:
        return "deterministic-fixture"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        self.requests.append(request)
        if self._output is None:
            result = await self._fallback.generate(request)
            return AgentProviderResult(
                output=result.output,
                provider=self.provider_name,
                model=self.model_name,
                input_tokens=120,
                output_tokens=40,
                estimated_cost_cny=0.01,
            )
        return AgentProviderResult(
            output=self._output,
            provider=self.provider_name,
            model=self.model_name,
            input_tokens=120,
            output_tokens=40,
            estimated_cost_cny=0.01,
        )


async def evaluate_m5_suite(
    session_factory: async_sessionmaker[AsyncSession],
    search_repository: LegalSearchRepository,
    datasets: LoadedM5Datasets,
    settings: Settings,
    index_name: str,
    embedding_provider: EmbeddingProvider | None = None,
) -> M5EvalReport:
    legal_dataset: LegalEvalDataset = load_legal_eval_dataset(datasets.legal_path)
    async with session_factory() as session:
        legal_report = await evaluate_legal_retrieval(
            SqlAlchemyUnitOfWork(session),
            search_repository,
            legal_dataset,
            index_name,
            embedding_provider,
        )
        await session.commit()

    results: list[EvalCaseResult] = []
    for procedure_case in datasets.procedure.cases:
        results.append(await _evaluate_procedure_case(session_factory, procedure_case, settings))
    for participant_case in datasets.participants.cases:
        results.append(
            await _evaluate_participant_case(session_factory, participant_case, settings)
        )

    legal_trace_ids = [item.trace_id for item in legal_report.cases if item.trace_id is not None]
    for end_to_end_case in datasets.end_to_end.cases:
        results.append(
            await _evaluate_end_to_end_case(
                session_factory, end_to_end_case, settings, legal_trace_ids
            )
        )

    checks = _metric_checks(results, legal_report)
    cost = _cost_metrics(results)
    subset_counts = {
        M5Subset.PROCEDURE_PERMISSIONS: len(datasets.procedure.cases),
        M5Subset.PARTICIPANT_BOUNDARIES: len(datasets.participants.cases),
        M5Subset.LEGAL_RAG: len(legal_report.cases),
        M5Subset.END_TO_END: len(datasets.end_to_end.cases),
    }
    return M5EvalReport(
        suite=datasets.manifest.suite,
        suite_version=datasets.manifest.version,
        generated_at=datetime.now(UTC),
        total_case_count=sum(subset_counts.values()),
        subset_counts=subset_counts,
        cases=results,
        legal_report=legal_report,
        checks=checks,
        cost=cost,
        passed=all(item.passed for item in checks),
    )


async def _evaluate_procedure_case(
    session_factory: async_sessionmaker[AsyncSession],
    item: ProcedureEvalCase,
    settings: Settings,
) -> EvalCaseResult:
    started = perf_counter()
    actual_code = "allowed"
    session_id: str | None = None
    try:
        async with session_factory() as session:
            unit_of_work = SqlAlchemyUnitOfWork(session)
            session_id = await create_court_session(
                unit_of_work,
                _session_create(item.case_id, item.user_role.value),
            )
            await _advance(unit_of_work, session_id, item.advance_count, settings)
            if item.pre_submitted_evidence_ids:
                session_model = await unit_of_work.court_sessions.get(session_id)
                if session_model is None:
                    raise RuntimeError("Eval session disappeared during evidence setup")
                submitted_by = (
                    "prosecution"
                    if session_model.phase == CourtPhase.PROSECUTION_EVIDENCE_AND_EXAMINATION.value
                    else "defense"
                )
                unit_of_work.court_sessions.add_evidence_submissions(
                    session_id,
                    item.pre_submitted_evidence_ids,
                    submitted_by,
                )
                unit_of_work.court_sessions.add_evidence_agenda_items(
                    session_id=session_id,
                    phase=session_model.phase,
                    evidence_ids=item.pre_submitted_evidence_ids,
                    submitted_by=submitted_by,
                    responding_role="defense" if submitted_by == "prosecution" else "prosecution",
                    submission_event_sequence=None,
                )
            for setup in item.setup_actions:
                await apply_session_action(unit_of_work, session_id, setup, settings)
            # 先保留完整前置场景；预期拒绝只回滚目标动作，报告中的会话可直接复现。
            await session.commit()
            await apply_session_action(unit_of_work, session_id, item.action, settings)
            await session.commit()
    except SessionServiceError as exc:
        actual_code = exc.code
    except Exception as exc:
        actual_code = f"unexpected:{type(exc).__name__}"
    passed = actual_code == item.expected_code
    return EvalCaseResult(
        id=item.id,
        subset=M5Subset.PROCEDURE_PERMISSIONS,
        metric=item.metric,
        expected={"code": item.expected_code, "allowed": item.expected_allowed},
        actual={"code": actual_code, "allowed": actual_code == "allowed"},
        passed=passed,
        failures=[] if passed else ["OUTCOME_MISMATCH"],
        session_id=session_id,
        latency_ms=_elapsed_ms(started),
    )


async def _evaluate_participant_case(
    session_factory: async_sessionmaker[AsyncSession],
    item: ParticipantEvalCase,
    settings: Settings,
) -> EvalCaseResult:
    started = perf_counter()
    provider = _EvalProvider(item.provider_output)
    actual_status = "rejected"
    actual_code: str | None = None
    statement_ids: list[str] = []
    consistency_status: str | None = None
    session_id: str | None = None
    trace_ids: list[str] = []
    input_tokens = output_tokens = repair_count = 0
    cost = 0.0
    context_text = ""
    try:
        async with session_factory() as session:
            unit_of_work = SqlAlchemyUnitOfWork(session)
            session_id = await create_court_session(
                unit_of_work, _session_create(item.case_id, item.user_role.value)
            )
            await _advance(unit_of_work, session_id, item.advance_count, settings)
            await session.commit()
            result = await execute_agent_turn(
                unit_of_work,
                session_id,
                AgentTurnRequest(
                    actor_role=item.actor_role,
                    participant_id=item.participant_id,
                    action=CourtAction(item.action),
                    target_id=item.target_id,
                ),
                provider,
                settings,
            )
            actual_status = result.status.value
            actual_code = result.error.code if result.error is not None else None
            trace_ids = [result.trace.trace_id]
            input_tokens = result.trace.input_tokens
            output_tokens = result.trace.output_tokens
            repair_count = result.trace.repair_count
            cost = result.trace.estimated_cost_cny
            traces = await unit_of_work.court_sessions.list_participant_statement_traces(session_id)
            if traces:
                statement_ids = traces[-1].supported_statement_ids
                consistency_status = traces[-1].consistency_status
            if provider.requests:
                context_text = provider.requests[0].context.model_dump_json()
            await session.commit()
    except AgentTurnServiceError as exc:
        actual_code = exc.code
    except Exception as exc:
        actual_code = f"unexpected:{type(exc).__name__}"

    leaked_tokens = [token for token in item.forbidden_context_tokens if token in context_text]
    failures: list[str] = []
    if actual_status != item.expected_status:
        failures.append("STATUS_MISMATCH")
    if actual_code != item.expected_code:
        failures.append("CODE_MISMATCH")
    if statement_ids != item.expected_statement_ids:
        failures.append("STATEMENT_TRACE_MISMATCH")
    if consistency_status != item.expected_consistency_status:
        failures.append("CONSISTENCY_STATUS_MISMATCH")
    if leaked_tokens:
        failures.append("UNAUTHORIZED_CONTEXT_LEAK")
    return EvalCaseResult(
        id=item.id,
        subset=M5Subset.PARTICIPANT_BOUNDARIES,
        metric=item.metric,
        expected={
            "status": item.expected_status,
            "code": item.expected_code,
            "statement_ids": item.expected_statement_ids,
            "consistency_status": item.expected_consistency_status,
        },
        actual={
            "status": actual_status,
            "code": actual_code,
            "statement_ids": statement_ids,
            "consistency_status": consistency_status,
            "leaked_tokens": leaked_tokens,
        },
        passed=not failures,
        failures=failures,
        session_id=session_id,
        trace_ids=trace_ids,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_cny=cost,
        latency_ms=_elapsed_ms(started),
        repair_count=repair_count,
    )


async def _evaluate_end_to_end_case(
    session_factory: async_sessionmaker[AsyncSession],
    item: EndToEndEvalCase,
    settings: Settings,
    legal_trace_ids: list[str],
) -> EvalCaseResult:
    started = perf_counter()
    failures: list[str] = []
    session_id: str | None = None
    actual: dict[str, Any] = {}
    trace_ids: list[str] = []
    input_tokens = output_tokens = repair_count = 0
    cost = 0.0
    try:
        async with session_factory() as session:
            unit_of_work = SqlAlchemyUnitOfWork(session)
            session_id = await create_court_session(
                unit_of_work, _session_create(item.case_id, item.user_role.value)
            )
            await _advance(unit_of_work, session_id, 2, settings)
            actor_role = (
                AgentRole.DEFENSE
                if item.user_role.value == "prosecution"
                else AgentRole.PROSECUTION
            )
            agent_result = await execute_agent_turn(
                unit_of_work,
                session_id,
                AgentTurnRequest(
                    actor_role=actor_role,
                    action=CourtAction.MAKE_STATEMENT,
                ),
                _EvalProvider(None),
                settings,
            )
            trace_ids.append(agent_result.trace.trace_id)
            input_tokens += agent_result.trace.input_tokens
            output_tokens += agent_result.trace.output_tokens
            repair_count += agent_result.trace.repair_count
            cost += agent_result.trace.estimated_cost_cny

            evidence_phase = 3 if item.user_role.value == "prosecution" else 4
            await _advance(unit_of_work, session_id, evidence_phase - 2, settings)
            await apply_session_action(
                unit_of_work,
                session_id,
                _action("submit_evidence", evidence_ids=item.evidence_ids),
                settings,
            )
            while True:
                view = await get_session_view(unit_of_work, session_id)
                if view is None:
                    raise RuntimeError("Eval session disappeared")
                if view.phase is CourtPhase.LEGAL_ANALYSIS:
                    break
                await apply_session_action(
                    unit_of_work, session_id, _action("advance_phase"), settings
                )
            review = await generate_court_review(
                unit_of_work,
                session_id,
                CourtReviewGenerateRequest(legal_search_trace_ids=legal_trace_ids),
            )
            await _advance(unit_of_work, session_id, 2, settings)
            final_view = await get_session_view(unit_of_work, session_id)
            if final_view is None:
                raise RuntimeError("Eval session disappeared after completion")
            actual = {
                "phase": final_view.phase.value,
                "status": final_view.status,
                "element_count": len(review.element_findings),
                "all_elements_cited": all(item.citations for item in review.element_findings),
                "total_score": review.total_score,
                "score_dimension_keys": sorted(item.key for item in review.score_dimensions),
                "recommendation_count": len(review.recommendations),
                "conclusion": review.conclusion,
            }
            if final_view.phase is not CourtPhase.COMPLETED or final_view.status != "completed":
                failures.append("SESSION_NOT_COMPLETED")
            if len(review.element_findings) != 6:
                failures.append("ELEMENT_COVERAGE_INCOMPLETE")
            if not all(element.citations for element in review.element_findings):
                failures.append("LEGAL_TRACEABILITY_INCOMPLETE")
            if review.conclusion is not None:
                failures.append("DEVELOPMENT_CONCLUSION_NOT_BLOCKED")
            expected_score_keys = {
                "issue_closure",
                "legal_authority_coverage",
                "opponent_evidence_response",
                "priority_evidence_submission",
            }
            if {item.key for item in review.score_dimensions} != expected_score_keys:
                failures.append("LEARNING_SCORE_REPORT_INCOMPLETE")
            await session.commit()
    except Exception as exc:
        failures.append(f"UNEXPECTED_{type(exc).__name__}")
        actual["error"] = str(exc)
    return EvalCaseResult(
        id=item.id,
        subset=M5Subset.END_TO_END,
        metric="end_to_end_completion",
        expected={
            "phase": CourtPhase.COMPLETED.value,
            "status": "completed",
            "element_count": 6,
            "all_elements_cited": True,
            "score_dimension_keys": [
                "issue_closure",
                "legal_authority_coverage",
                "opponent_evidence_response",
                "priority_evidence_submission",
            ],
            "conclusion": None,
        },
        actual=actual,
        passed=not failures,
        failures=failures,
        session_id=session_id,
        trace_ids=trace_ids,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_cny=cost,
        latency_ms=_elapsed_ms(started),
        repair_count=repair_count,
    )


def _metric_checks(results: list[EvalCaseResult], legal_report: Any) -> list[EvalMetricCheck]:
    def ratio(metric: str) -> float:
        selected = [item for item in results if item.metric == metric]
        return sum(item.passed for item in selected) / len(selected) if selected else 1.0

    participant = [item for item in results if item.subset is M5Subset.PARTICIPANT_BOUNDARIES]
    procedure = [item for item in results if item.subset is M5Subset.PROCEDURE_PERMISSIONS]
    first_pass_rate = (
        sum(item.repair_count == 0 for item in participant) / len(participant)
        if participant
        else 1.0
    )
    end_to_end = [item for item in results if item.subset is M5Subset.END_TO_END]
    element_coverage = sum(item.actual.get("element_count") == 6 for item in end_to_end) / len(
        end_to_end
    )
    traceability = sum(item.actual.get("all_elements_cited") is True for item in end_to_end) / len(
        end_to_end
    )
    nonexistent_evidence = [item for item in procedure if item.metric == "nonexistent_evidence"]
    illegal_stage = [item for item in procedure if item.metric == "illegal_stage"]
    leak_count = sum(bool(item.actual.get("leaked_tokens")) for item in participant)
    participant_compliance = sum(item.passed for item in participant) / len(participant)
    specs = [
        (
            "evidence_reference_accuracy",
            ">=95%",
            sum(item.passed for item in procedure) / len(procedure),
            0.95,
            False,
        ),
        (
            "nonexistent_evidence_interception_rate",
            "100%",
            sum(item.passed for item in nonexistent_evidence) / len(nonexistent_evidence),
            1,
            True,
        ),
        (
            "unauthorized_information_leak_rate",
            "0%",
            leak_count / len(participant),
            0,
            True,
        ),
        (
            "illegal_stage_interception_rate",
            "100%",
            sum(item.passed for item in illegal_stage) / len(illegal_stage),
            1,
            True,
        ),
        ("participant_boundary_compliance_rate", ">=95%", participant_compliance, 0.95, False),
        ("schema_first_pass_rate", ">=90%", first_pass_rate, 0.9, False),
        ("end_to_end_completion_rate", ">=80%", ratio("end_to_end_completion"), 0.8, False),
        (
            "legal_citation_authenticity",
            "100%",
            float(all(item.passed for item in legal_report.cases)),
            1,
            True,
        ),
        ("element_coverage_rate", "100%", element_coverage, 1, False),
        ("fact_to_law_traceability_rate", "100%", traceability, 1, False),
        (
            "cross_jurisdiction_mixing_rate",
            "0%",
            1 - legal_report.metrics.validity_filter_accuracy,
            0,
            True,
        ),
        ("rag_recall_at_5", ">=90%", legal_report.metrics.recall_at_k, 0.9, False),
        ("rag_precision_at_5", ">=70%", legal_report.metrics.precision_at_k, 0.7, False),
        (
            "validity_filter_accuracy",
            "100%",
            legal_report.metrics.validity_filter_accuracy,
            1,
            True,
        ),
        ("refusal_accuracy", ">=95%", legal_report.metrics.refusal_accuracy, 0.95, False),
    ]
    checks: list[EvalMetricCheck] = []
    for name, target, actual, threshold, blocking in specs:
        passed = actual >= threshold if target.startswith(">=") else actual == threshold
        checks.append(
            EvalMetricCheck(
                name=name,
                target=target,
                actual=actual,
                passed=passed,
                blocking=blocking,
            )
        )
    return checks


def _cost_metrics(results: list[EvalCaseResult]) -> M5CostMetrics:
    calls = [item for item in results if item.input_tokens or item.output_tokens]
    return M5CostMetrics(
        agent_call_count=len(calls),
        input_tokens=sum(item.input_tokens for item in calls),
        output_tokens=sum(item.output_tokens for item in calls),
        estimated_cost_cny=sum(item.estimated_cost_cny for item in calls),
        average_agent_latency_ms=(
            sum(item.latency_ms for item in calls) / len(calls) if calls else 0
        ),
        repair_rate=(sum(item.repair_count > 0 for item in calls) / len(calls) if calls else 0),
    )


async def _advance(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    count: int,
    settings: Settings,
) -> None:
    for _ in range(count):
        await apply_session_action(unit_of_work, session_id, _action("advance_phase"), settings)


def _session_create(case_id: str, user_role: str) -> SessionCreate:
    return SessionCreate(case_id=case_id, user_role=UserRole(user_role))


def _action(action: str, **kwargs: Any) -> SessionActionRequest:
    return SessionActionRequest(action=CourtAction(action), **kwargs)


def _elapsed_ms(started: float) -> float:
    return max(0, (perf_counter() - started) * 1_000)
