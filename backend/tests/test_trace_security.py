import json

import pytest
from pydantic import SecretStr, ValidationError

from mootcourt.core.config import Settings
from mootcourt.core.security import diagnostics_access_allowed
from mootcourt.core.trace_security import protect_agent_trace_payloads


def test_redacted_trace_preserves_structure_but_not_natural_language() -> None:
    request = {
        "context": {
            "case": {"case_id": "CASE-001", "summary": "不能写入数据库的案件摘要"},
            "actor_role": "defense",
        },
        "instruction": "这是一段不能被原样保留的用户指令",
    }
    response = {
        "kind": "advocate",
        "speech": "一段不能被原样保留的发言",
        "claims": [{"citations": [{"quote": "不能保留的引文"}]}],
    }

    redacted_request, redacted_response = protect_agent_trace_payloads(
        request,
        response,
        mode="redacted",
        hmac_key="h" * 32,
    )
    serialized = json.dumps([redacted_request, redacted_response], ensure_ascii=False)

    assert "不能写入数据库的案件摘要" not in serialized
    assert "不能被原样保留的用户指令" not in serialized
    assert "不能保留的引文" not in serialized
    assert redacted_request["payload"]["context"]["case"]["case_id"] == "CASE-001"
    assert redacted_request["payload"]["context"]["case"]["summary"]["redacted"] is True
    assert redacted_request["payload"]["context"]["case"]["summary"]["hmac_sha256"]


def test_trace_payload_modes_are_explicit() -> None:
    request = {"instruction": "secret"}
    response = {"speech": "secret response"}

    full_request, full_response = protect_agent_trace_payloads(
        request, response, mode="full", hmac_key=""
    )
    empty_request, empty_response = protect_agent_trace_payloads(
        request, response, mode="none", hmac_key=""
    )

    assert full_request == request
    assert full_response == response
    assert empty_request == {"schema_version": "agent-trace-none-v1"}
    assert empty_response is None


def test_production_settings_require_diagnostic_and_redaction_secrets() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="production")
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            diagnostics_api_key=SecretStr("d" * 32),
            agent_trace_payload_mode="full",
        )

    settings = Settings(
        app_env="production",
        diagnostics_api_key=SecretStr("d" * 32),
        trace_redaction_hmac_key=SecretStr("h" * 32),
        supabase_url="https://example.supabase.co",
        supabase_jwt_issuer="https://example.supabase.co/auth/v1",
    )
    assert diagnostics_access_allowed("d" * 32, settings) is True
    assert diagnostics_access_allowed("wrong", settings) is False
    assert diagnostics_access_allowed(None, settings) is False
