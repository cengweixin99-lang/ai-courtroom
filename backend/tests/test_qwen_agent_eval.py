from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mootcourt.agents.factory import AgentProviderConfigurationError, build_agent_provider
from mootcourt.agents.openai_compatible import OpenAICompatibleProvider
from mootcourt.agents.providers import AgentProviderRequest, AgentProviderResult, FakeAgentProvider
from mootcourt.core.config import Settings
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.qwen_agent_eval import load_qwen_agent_eval_dataset
from mootcourt.services.case_importer import import_case_package
from mootcourt.services.qwen_agent_eval import (
    _case_failures,
    _provider_http_status,
    evaluate_qwen_agent_suite,
)

ROOT = Path(__file__).parents[2]
DATASET = ROOT / "evals" / "qwen_agent" / "cases.json"
CASE_PACKAGE = ROOT / "data" / "authoring" / "CASE-001"


class CalibratedFakeProvider(FakeAgentProvider):
    def __init__(self, *, actual_input_tokens: int, estimated_input_tokens: int) -> None:
        self._actual_input_tokens = actual_input_tokens
        self._estimated_input_tokens = estimated_input_tokens

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        result = await super().generate(request)
        return replace(
            result,
            input_tokens=self._actual_input_tokens,
            estimated_input_tokens=self._estimated_input_tokens,
            provider_request_count=1,
        )


def test_qwen_agent_dataset_covers_real_model_scenarios() -> None:
    dataset = load_qwen_agent_eval_dataset(DATASET)

    assert dataset.dataset == "qwen_agent_quality"
    assert len(dataset.cases) == 16
    assert len({item.id for item in dataset.cases}) == 16
    joined_citation_case = next(item for item in dataset.cases if item.id == "QWEN-ADV-008")
    assert joined_citation_case.required_cited_evidence_ids == ["E07", "E09", "E11"]
    assert joined_citation_case.max_repair_count == 0
    assert any(item.required_cited_evidence_ids for item in dataset.cases)
    assert any(item.expected_refusal is True for item in dataset.cases)
    assert any(item.forbidden_output_tokens for item in dataset.cases)


def test_provider_http_status_only_extracts_a_valid_status_code() -> None:
    assert _provider_http_status("model endpoint returned HTTP 403: denied") == 403
    assert _provider_http_status("unavailable without a response") is None


def test_qwen_agent_dataset_loader_reports_missing_and_invalid_json(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing Qwen Agent Eval dataset"):
        load_qwen_agent_eval_dataset(tmp_path / "missing.json")

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid Qwen Agent Eval JSON"):
        load_qwen_agent_eval_dataset(invalid)


def test_qwen_agent_failure_matrix_reports_every_quality_dimension() -> None:
    base = load_qwen_agent_eval_dataset(DATASET).cases[0]
    item = base.model_copy(
        update={
            "required_statement_ids": ["S01"],
            "expected_consistency_status": "SUPPORTED_BY_PRIOR_STATEMENT",
            "required_cited_evidence_ids": ["E01"],
            "min_claim_count": 1,
            "expected_refusal": True,
            "forbidden_output_tokens": ["SYSTEM_SECRET"],
            "max_repair_count": 0,
        }
    )

    failures = _case_failures(
        item,
        actual_status="failed",
        actual_code="unexpected_error",
        statement_ids=[],
        consistency_status=None,
        cited_evidence_ids=[],
        claim_count=0,
        refused=False,
        scanned_output_text="SYSTEM_SECRET",
        repair_count=1,
    )

    assert failures == [
        "STATUS_MISMATCH",
        "CODE_MISMATCH",
        "REQUIRED_STATEMENT_MISSING",
        "CONSISTENCY_STATUS_MISMATCH",
        "REQUIRED_EVIDENCE_CITATION_MISSING",
        "CLAIM_COUNT_TOO_LOW",
        "REFUSAL_MISMATCH",
        "FORBIDDEN_OUTPUT_LEAK",
        "SCHEMA_REPAIR_EXCEEDED",
    ]


def test_qwen_agent_forbidden_scan_covers_non_visible_output_fields() -> None:
    base = load_qwen_agent_eval_dataset(DATASET).cases[0]
    item = base.model_copy(update={"forbidden_output_tokens": ["private_background"]})

    failures = _case_failures(
        item,
        actual_status="succeeded",
        actual_code=None,
        statement_ids=[],
        consistency_status=None,
        cited_evidence_ids=[],
        claim_count=1,
        refused=True,
        scanned_output_text='{"answer":"合法回答","refused_reason":"private_background"}',
        repair_count=0,
    )

    assert failures == ["FORBIDDEN_OUTPUT_LEAK"]


def test_real_qwen_eval_rejects_fake_provider_configuration() -> None:
    with pytest.raises(AgentProviderConfigurationError, match="real Qwen Agent Eval"):
        build_agent_provider(Settings(llm_provider="fake"), allow_fake=False)


def test_qwen_factory_uses_deterministic_non_thinking_defaults() -> None:
    provider = build_agent_provider(
        Settings(
            llm_provider="openai-compatible",
            llm_model="qwen3.7-max",
            llm_api_key=SecretStr("test-key"),
        )
    )

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider._enable_thinking is False
    assert provider._temperature == 0


async def test_qwen_agent_runner_uses_formal_agent_validation_chain(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        await import_case_package(unit_of_work, CASE_PACKAGE)
        await unit_of_work.commit()

    report = await evaluate_qwen_agent_suite(
        session_factory,
        load_qwen_agent_eval_dataset(DATASET),
        FakeAgentProvider(),
        Settings(llm_provider="fake"),
        {"QWEN-ADV-003", "QWEN-PART-001"},
    )

    assert report.passed
    assert report.case_count == 2
    assert report.cost.model_call_count == 2
    assert report.cost.normalization_count == 1
    assert report.cost.normalization_rate == 0.5
    assert report.token_calibration.sample_count == 0
    assert report.token_calibration.passed is None
    by_id = {item.id: item for item in report.cases}
    assert by_id["QWEN-ADV-003"].actual["cited_evidence_ids"] == ["E01"]
    assert by_id["QWEN-PART-001"].actual["statement_ids"] == ["W01-S01"]
    assert all(item.trace_id for item in report.cases)


async def test_qwen_agent_token_calibration_blocks_underestimated_context(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        await import_case_package(unit_of_work, CASE_PACKAGE)
        await unit_of_work.commit()

    report = await evaluate_qwen_agent_suite(
        session_factory,
        load_qwen_agent_eval_dataset(DATASET),
        CalibratedFakeProvider(actual_input_tokens=100, estimated_input_tokens=80),
        Settings(llm_provider="fake"),
        {"QWEN-ADV-003"},
    )

    assert report.cases[0].provider_request_count == 1
    assert report.cases[0].input_token_estimation_ratio == 0.8
    assert report.cases[0].input_token_underestimated is True
    assert report.token_calibration.underestimation_count == 1
    assert report.token_calibration.max_underestimation_ratio == pytest.approx(0.2)
    assert report.token_calibration.passed is False
    assert report.passed is False


async def test_qwen_agent_runner_reports_refusal_failure_without_masking_it(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        await import_case_package(unit_of_work, CASE_PACKAGE)
        await unit_of_work.commit()

    report = await evaluate_qwen_agent_suite(
        session_factory,
        load_qwen_agent_eval_dataset(DATASET),
        FakeAgentProvider(),
        Settings(llm_provider="fake"),
        {"QWEN-PART-006"},
    )

    assert not report.passed
    assert report.cases[0].failures == [
        "CONSISTENCY_STATUS_MISMATCH",
        "REFUSAL_MISMATCH",
    ]
    refusal_check = next(item for item in report.checks if item.name == "explicit_refusal_accuracy")
    assert refusal_check.actual == 0
    assert refusal_check.blocking


async def test_qwen_agent_runner_rejects_unknown_case_selection(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(ValueError, match="unknown Qwen Agent Eval case IDs"):
        await evaluate_qwen_agent_suite(
            session_factory,
            load_qwen_agent_eval_dataset(DATASET),
            FakeAgentProvider(),
            Settings(llm_provider="fake"),
            {"QWEN-MISSING"},
        )
