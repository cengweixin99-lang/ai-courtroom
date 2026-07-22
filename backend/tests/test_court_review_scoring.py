from datetime import UTC, date, datetime
from types import SimpleNamespace

from mootcourt.agents.providers import FakeAgentProvider
from mootcourt.schemas.case_package import RoleMaterial
from mootcourt.schemas.reviews import (
    CourtReviewReport,
    ElementFindingStatus,
    FactFindingStatus,
    ReviewElementFinding,
    ReviewFactFinding,
    ReviewLegalCitation,
    ReviewTurnCheck,
    ReviewTurnDiagnostic,
    TurnQualityEvaluation,
    TurnQualityEvaluationGenerateRequest,
)
from mootcourt.services.court_reviews import (
    _score_courtroom_learning,
    _turn_diagnostics,
    _validate_turn_evaluations,
    _weighted_total,
    generate_turn_quality_evaluation,
    get_turn_quality_evaluation,
)


def test_learning_score_identifies_missing_evidence_and_deferred_response() -> None:
    """评分必须能把可操作的庭审遗漏定位到对应材料。"""

    dimensions, recommendations = _score_courtroom_learning(
        user_role="prosecution",
        role_materials=[
            RoleMaterial(
                id="RM-P",
                role="prosecution",
                visibility=["prosecution"],
                title="公诉材料",
                objectives=[],
                priority_evidence_ids=["E03", "E04"],
                known_weaknesses=[],
            )
        ],
        evidence_submissions=[SimpleNamespace(evidence_id="E03", submitted_by="prosecution")],
        evidence_agenda=[
            SimpleNamespace(evidence_id="E10", responding_role="prosecution", status="deferred")
        ],
        required_source_ids={"LAW-01"},
        citations={
            "LAW-01": ReviewLegalCitation(
                source_id="LAW-01",
                instrument_title="中华人民共和国刑法",
                article_number="第二百六十四条",
                text="盗窃公私财物。",
                official_source_url="https://flk.npc.gov.cn/",
                version_hash=None,
                trace_id="trace-001",
            )
        },
        fact_findings=[
            ReviewFactFinding(
                fact_id="F01",
                description="争议事实",
                status=FactFindingStatus.DISPUTED,
                submitted_supporting_evidence_ids=["E03"],
                submitted_contradicting_evidence_ids=[],
                appeared_statement_ids=[],
                challenged_evidence_ids=["E03"],
            )
        ],
        element_findings=[
            ReviewElementFinding(
                element_id="ELEM-01",
                description="构成要件一",
                status=ElementFindingStatus.SATISFIED,
                supporting_fact_ids=["F01"],
                contradicting_fact_ids=[],
                legal_source_ids=["LAW-01"],
                citations=[],
            ),
            ReviewElementFinding(
                element_id="ELEM-02",
                description="构成要件二",
                status=ElementFindingStatus.INSUFFICIENT,
                supporting_fact_ids=[],
                contradicting_fact_ids=[],
                legal_source_ids=["LAW-01"],
                citations=[],
            ),
        ],
    )

    scores = {item.key: item.score for item in dimensions}
    assert scores == {
        "priority_evidence_submission": 50,
        "opponent_evidence_response": 0,
        "legal_authority_coverage": 100,
        "issue_closure": 50,
    }
    assert _weighted_total(dimensions) == 45

    by_id = {item.id: item for item in recommendations}
    assert by_id["missing-priority-evidence"].related_evidence_ids == ["E04"]
    assert by_id["deferred-opponent-evidence"].related_evidence_ids == ["E10"]
    assert by_id["unresolved-facts"].related_fact_ids == ["F01"]
    assert by_id["unclosed-legal-elements"].related_element_ids == ["ELEM-02"]


def test_learning_score_does_not_penalize_non_applicable_obligations() -> None:
    """没有优先证据或对方举证时，评分应显示不适用而非人为扣分。"""

    dimensions, recommendations = _score_courtroom_learning(
        user_role="defense",
        role_materials=[],
        evidence_submissions=[],
        evidence_agenda=[],
        required_source_ids=set(),
        citations={},
        fact_findings=[],
        element_findings=[],
    )

    assert all(item.score == 100 for item in dimensions)
    assert _weighted_total(dimensions) == 100
    assert recommendations == []


def test_turn_diagnostics_use_structured_evidence_anchors() -> None:
    events = [
        SimpleNamespace(
            sequence_number=12,
            actor_role="prosecution",
            phase="COURT_DEBATE_PROSECUTION",
            action="make_statement",
            payload={"content": "结合证据发表公诉意见。", "evidence_ids": ["E03"]},
        ),
        SimpleNamespace(
            sequence_number=13,
            actor_role="prosecution",
            phase="COURT_DEBATE_PROSECUTION",
            action="make_statement",
            payload={"content": "未选择证据的公诉意见。", "evidence_ids": []},
        ),
        SimpleNamespace(
            sequence_number=14,
            actor_role="defense",
            phase="COURT_DEBATE_DEFENSE",
            action="make_statement",
            payload={"content": "对方席位发言。", "evidence_ids": ["E10"]},
        ),
    ]

    diagnostics = _turn_diagnostics(
        user_role="prosecution",
        events=events,
        evidence_fact_ids={"E03": {"F03"}, "E10": {"F10"}},
    )

    assert [item.event_sequence_number for item in diagnostics] == [12, 13]
    assert diagnostics[0].score == 100
    assert diagnostics[0].evidence_ids == ["E03"]
    assert diagnostics[0].fact_ids == ["F03"]
    assert diagnostics[0].recommendation is None
    assert diagnostics[1].score == 40
    assert diagnostics[1].recommendation is not None


def test_turn_evaluation_disables_rewrite_without_complete_anchors() -> None:
    diagnostic = ReviewTurnDiagnostic(
        event_sequence_number=1,
        actor_role="defense",
        phase="COURT_DEBATE_DEFENSE",
        action="make_statement",
        score=40,
        evidence_ids=[],
        fact_ids=[],
        checks=[ReviewTurnCheck(key="content", label="陈述", passed=True, detail="已陈述")],
    )
    evaluation = TurnQualityEvaluation(
        event_sequence_number=1,
        organization_score=30,
        responsiveness_score=20,
        advocacy_score=20,
        rewritten_example="模型虚构的改写内容",
    )

    normalized = _validate_turn_evaluations([evaluation], {1: diagnostic}, [1])

    assert normalized[0].rewritten_example is None


async def test_generate_turn_quality_evaluation_persists_independent_report() -> None:
    """深度点评只保存独立报告，并沿用确定性诊断提供的证据与事实锚点。"""

    diagnostic = ReviewTurnDiagnostic(
        event_sequence_number=1,
        actor_role="defense",
        phase="COURT_DEBATE_DEFENSE",
        action="make_statement",
        score=80,
        evidence_ids=["E03"],
        fact_ids=["F03"],
        checks=[ReviewTurnCheck(key="anchor", label="证据锚点", passed=True, detail="已关联")],
    )
    review = CourtReviewReport(
        id="review-001",
        session_id="session-001",
        case_id="CASE-001",
        package_version="1.0.0",
        jurisdiction="中华人民共和国-上海市",
        law_as_of_date=date(2026, 7, 22),
        burden_of_proof="公诉机关承担证明责任",
        standard_of_proof="事实清楚，证据确实、充分",
        user_role="defense",
        fact_findings=[],
        element_findings=[],
        turn_diagnostics=[diagnostic],
        unresolved_issue_ids=[],
        deterministic_conclusion_allowed=False,
        conclusion=None,
        disclaimer="仅供模拟教学使用。",
        legal_search_trace_ids=[],
        event_sequence_number=1,
        created_at=datetime.now(UTC),
    )

    class RecordingCourtSessionRepository:
        def __init__(self) -> None:
            self.saved: dict[str, object] | None = None

        async def get_court_review(self, session_id: str) -> object:
            assert session_id == "session-001"
            return SimpleNamespace(id="review-001", report=review.model_dump(mode="json"))

        async def get_court_review_evaluation(self, review_id: str) -> None:
            assert review_id == "review-001"
            return None

        async def list_events(self, session_id: str) -> list[object]:
            assert session_id == "session-001"
            return [SimpleNamespace(sequence_number=1, payload={"content": "围绕E03回应F03。"})]

        async def add_court_review_evaluation(self, **kwargs: object) -> None:
            self.saved = kwargs

    repository = RecordingCourtSessionRepository()
    unit_of_work = SimpleNamespace(court_sessions=repository)

    report = await generate_turn_quality_evaluation(
        unit_of_work,  # type: ignore[arg-type]
        "session-001",
        TurnQualityEvaluationGenerateRequest(),
        FakeAgentProvider(),
    )

    assert report.review_id == "review-001"
    assert report.evaluations[0].evidence_ids == ["E03"]
    assert report.evaluations[0].fact_ids == ["F03"]
    assert report.evaluations[0].rewritten_example is not None
    assert repository.saved is not None
    assert repository.saved["session_id"] == "session-001"
    assert await get_turn_quality_evaluation(unit_of_work, "session-001") is None  # type: ignore[arg-type]
