from typing import Any

from mootcourt.main import app

EXPECTED_OPERATIONS = {
    ("/api/v1/health", "get"): "health_check",
    ("/api/v1/cases", "get"): "list_case_packages",
    ("/api/v1/cases/{case_id}", "get"): "get_role_scoped_case",
    ("/api/v1/sessions", "post"): "create_court_session",
    ("/api/v1/sessions/{session_id}", "get"): "get_court_session",
    ("/api/v1/sessions/{session_id}/events", "get"): "list_court_session_events",
    (
        "/api/v1/sessions/{session_id}/evidence-statuses",
        "get",
    ): "list_session_evidence_statuses",
    (
        "/api/v1/sessions/{session_id}/evidence-agenda",
        "get",
    ): "list_session_evidence_agenda",
    (
        "/api/v1/sessions/{session_id}/procedural-requests",
        "get",
    ): "list_session_procedural_requests",
    (
        "/api/v1/sessions/{session_id}/procedural-requests/{request_id}/resolution",
        "post",
    ): "resolve_session_procedural_request",
    (
        "/api/v1/sessions/{session_id}/evidence-fact-summary",
        "get",
    ): "get_session_evidence_fact_summary",
    ("/api/v1/sessions/{session_id}/actions", "post"): "apply_court_session_action",
    ("/api/v1/sessions/{session_id}/agent-turns", "post"): "execute_agent_turn",
    ("/api/v1/sessions/{session_id}/auto-step", "post"): "run_automatic_court_step",
    ("/api/v1/sessions/{session_id}/auto-step/stream", "post"): "stream_automatic_court_step",
    ("/api/v1/sessions/{session_id}/traces", "get"): "list_agent_traces",
    (
        "/api/v1/sessions/{session_id}/participant-statement-traces",
        "get",
    ): "list_participant_statement_traces",
    (
        "/api/v1/sessions/{session_id}/participant-statement-traces/{trace_id}/resolution",
        "post",
    ): "resolve_session_new_statement",
    ("/api/v1/sessions/{session_id}/review", "post"): "generate_session_court_review",
    ("/api/v1/sessions/{session_id}/review", "get"): "get_session_court_review",
    (
        "/api/v1/sessions/{session_id}/review/turn-evaluation",
        "post",
    ): "generate_session_turn_quality_evaluation",
    (
        "/api/v1/sessions/{session_id}/review/turn-evaluation",
        "get",
    ): "get_session_turn_quality_evaluation",
    ("/api/v1/legal/search", "post"): "search_case_law",
    ("/api/v1/legal/search-traces/{trace_id}", "get"): "get_legal_search_trace",
    ("/api/v1/legal/citations/validate", "post"): "validate_legal_citations",
}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def _operations(schema: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (path, method): operation
        for path, path_item in schema["paths"].items()
        for method, operation in path_item.items()
        if method in HTTP_METHODS
    }


def test_openapi_documents_every_runtime_operation() -> None:
    operations = _operations(app.openapi())

    assert set(operations) == set(EXPECTED_OPERATIONS)
    assert {
        key: operation["operationId"] for key, operation in operations.items()
    } == EXPECTED_OPERATIONS
    assert len({operation["operationId"] for operation in operations.values()}) == len(operations)
    assert all(operation.get("summary") for operation in operations.values())
    assert all(operation.get("description") for operation in operations.values())


def test_action_endpoint_documents_business_errors() -> None:
    operation = app.openapi()["paths"]["/api/v1/sessions/{session_id}/actions"]["post"]

    assert {"403", "404", "409", "422"}.issubset(operation["responses"])


def test_agent_endpoint_documents_failure_trace_response() -> None:
    operation = app.openapi()["paths"]["/api/v1/sessions/{session_id}/agent-turns"]["post"]

    assert {"403", "404", "409", "422", "429", "502", "503"}.issubset(operation["responses"])
    idempotency = next(
        item for item in operation["parameters"] if item["name"] == "Idempotency-Key"
    )
    assert idempotency["in"] == "header"
    assert idempotency["required"] is False


def test_action_request_fields_have_descriptions() -> None:
    properties = app.openapi()["components"]["schemas"]["SessionActionRequest"]["properties"]

    assert {
        "action",
        "target_id",
        "evidence_ids",
        "content",
        "procedural_request_type",
        "target_event_sequence",
        "challenge_dimensions",
    } == set(properties)
    assert all(field.get("description") for field in properties.values())


def test_openapi_tags_have_descriptions() -> None:
    tags = app.openapi()["tags"]

    assert {tag["name"] for tag in tags} == {
        "system",
        "cases",
        "sessions",
        "agents",
        "legal-search",
    }
    assert all(tag.get("description") for tag in tags)


def test_legal_search_documents_hybrid_scores() -> None:
    schemas = app.openapi()["components"]["schemas"]
    hit_properties = schemas["LegalSearchHit"]["properties"]
    response_properties = schemas["LegalSearchResponse"]["properties"]

    assert hit_properties["score"]["description"]
    assert hit_properties["bm25_score"]["description"]
    assert hit_properties["vector_score"]["description"]
    assert response_properties["retrieval_mode"]["description"]
    assert response_properties["embedding_version"]["description"]
    assert response_properties["trace_id"]["description"]
