from __future__ import annotations

import hashlib
import hmac
from typing import Any, Literal

TracePayloadMode = Literal["full", "redacted", "none"]

_SAFE_STRING_FIELDS = {
    "action",
    "actor_role",
    "case_id",
    "certainty",
    "challenge_dimensions",
    "claim_type",
    "kind",
    "package_version",
    "phase",
    "requested_action",
    "speaker_role",
    "status",
}


def protect_agent_trace_payloads(
    request_payload: dict[str, Any],
    response_payload: dict[str, Any] | None,
    *,
    mode: TracePayloadMode,
    hmac_key: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """按配置保留 Trace 结构，同时避免把自然语言正文直接写入运行库。"""

    if mode == "full":
        return request_payload, response_payload
    if mode == "none":
        return {"schema_version": "agent-trace-none-v1"}, None
    return (
        {
            "schema_version": "agent-trace-redacted-v1",
            "payload": _redact_value(request_payload, hmac_key=hmac_key),
        },
        (
            {
                "schema_version": "agent-trace-redacted-v1",
                "payload": _redact_value(response_payload, hmac_key=hmac_key),
            }
            if response_payload is not None
            else None
        ),
    )


def protected_trace_error_message(
    error_message: str | None,
    *,
    mode: TracePayloadMode,
) -> str | None:
    """错误码已承担诊断分类；非 full 模式不落库可能包含上游文本的错误详情。"""

    return error_message if mode == "full" else None


def _redact_value(value: Any, *, hmac_key: str, field_name: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _redact_value(item, hmac_key=hmac_key, field_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item, hmac_key=hmac_key, field_name=field_name) for item in value]
    if isinstance(value, str):
        if _safe_string_field(field_name):
            return value
        return _redacted_string(value, hmac_key)
    return value


def _safe_string_field(field_name: str | None) -> bool:
    if field_name is None:
        return False
    return (
        field_name in _SAFE_STRING_FIELDS
        or field_name == "id"
        or field_name.endswith(("_id", "_ids"))
    )


def _redacted_string(value: str, hmac_key: str) -> dict[str, Any]:
    encoded = value.encode("utf-8")
    if hmac_key:
        fingerprint_name = "hmac_sha256"
        fingerprint = hmac.new(hmac_key.encode("utf-8"), encoded, hashlib.sha256).hexdigest()
    else:
        # 仅供非生产开发环境使用；生产 redacted 模式由 Settings 强制要求 HMAC 密钥。
        fingerprint_name = "sha256"
        fingerprint = hashlib.sha256(encoded).hexdigest()
    return {
        "redacted": True,
        "utf8_bytes": len(encoded),
        fingerprint_name: fingerprint,
    }
