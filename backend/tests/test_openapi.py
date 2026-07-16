from typing import Any

from mootcourt.main import app

EXPECTED_OPERATIONS = {
    ("/api/v1/health", "get"): "health_check",
    ("/api/v1/cases", "get"): "list_case_packages",
    ("/api/v1/cases/{case_id}", "get"): "get_role_scoped_case",
    ("/api/v1/sessions", "post"): "create_court_session",
    ("/api/v1/sessions/{session_id}", "get"): "get_court_session",
    ("/api/v1/sessions/{session_id}/events", "get"): "list_court_session_events",
    ("/api/v1/sessions/{session_id}/actions", "post"): "apply_court_session_action",
    ("/api/v1/sessions/{session_id}/agent-turns", "post"): "execute_agent_turn",
    ("/api/v1/sessions/{session_id}/traces", "get"): "list_agent_traces",
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

    assert {"403", "404", "409", "422", "502"}.issubset(operation["responses"])


def test_action_request_fields_have_descriptions() -> None:
    properties = app.openapi()["components"]["schemas"]["SessionActionRequest"]["properties"]

    assert {"action", "target_id", "evidence_ids", "content"} == set(properties)
    assert all(field.get("description") for field in properties.values())


def test_openapi_tags_have_descriptions() -> None:
    tags = app.openapi()["tags"]

    assert {tag["name"] for tag in tags} == {"system", "cases", "sessions", "agents"}
    assert all(tag.get("description") for tag in tags)
