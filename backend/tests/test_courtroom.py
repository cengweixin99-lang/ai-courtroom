from mootcourt.domain.courtroom import (
    ActionRequest,
    CourtAction,
    CourtPhase,
    Role,
    next_phase,
    validate_action,
)


def test_illegal_action_is_rejected_before_agent_invocation() -> None:
    request = ActionRequest(role=Role.DEFENSE, action=CourtAction.SUBMIT_EVIDENCE)

    decision = validate_action(CourtPhase.COURT_OPENING, request)

    assert decision.allowed is False
    assert decision.invoke_agent is False
    assert decision.reason is not None


def test_legal_role_action_can_invoke_agent() -> None:
    request = ActionRequest(role=Role.PROSECUTION, action=CourtAction.MAKE_STATEMENT)

    decision = validate_action(CourtPhase.COURT_DEBATE_PROSECUTION, request)

    assert decision.allowed is True
    assert decision.invoke_agent is True


def test_phase_sequence_is_deterministic_and_terminal() -> None:
    assert next_phase(CourtPhase.COURT_OPENING) is CourtPhase.INDICTMENT_AND_DEFENDANT_STATEMENT
    assert next_phase(CourtPhase.COMPLETED) is CourtPhase.COMPLETED
