from __future__ import annotations

import argparse
import asyncio
import json
from typing import cast

from mootcourt.agents.factory import AgentProviderConfigurationError, build_agent_provider
from mootcourt.agents.openai_compatible import AgentProviderError
from mootcourt.agents.providers import StructuredAgentProvider, StructuredProviderRequest
from mootcourt.core.config import get_settings
from mootcourt.core.redis import dispose_redis

_PREFLIGHT_SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string", "enum": ["ok"]}},
    "required": ["status"],
    "additionalProperties": False,
}


async def _run(*, show_safe_detail: bool = False) -> bool:
    settings = get_settings()
    try:
        try:
            provider = cast(
                StructuredAgentProvider,
                build_agent_provider(settings, allow_fake=False),
            )
        except AgentProviderConfigurationError:
            print(json.dumps({"passed": False, "code": "configuration_error"}))
            return False
        try:
            result = await provider.generate_structured(
                StructuredProviderRequest(
                    messages=(
                        {
                            "role": "system",
                            "content": '仅输出 JSON 对象 {"status":"ok"}。',
                        },
                        {"role": "user", "content": "返回预检状态。"},
                    ),
                    schema_name="mootcourt_provider_preflight",
                    response_schema=_PREFLIGHT_SCHEMA,
                    fallback_output={"status": "ok"},
                )
            )
        except AgentProviderError as exc:
            diagnostics: dict[str, object] = {
                "passed": False,
                "code": exc.code,
                "http_status": exc.http_status,
                "provider_request_count": exc.provider_request_count,
            }
            if show_safe_detail:
                # The provider error is already parsed and length-limited before it reaches here.
                diagnostics["safe_detail"] = exc.message
            print(json.dumps(diagnostics))
            return False
        passed = result.output.get("status") == "ok"
        print(
            json.dumps(
                {
                    "passed": passed,
                    "provider": result.provider,
                    "model": result.model,
                    "provider_request_count": result.provider_request_count,
                }
            )
        )
        return passed
    finally:
        await dispose_redis()


def main() -> None:
    parser = argparse.ArgumentParser(description="Check OpenAI-compatible provider access safely")
    parser.add_argument(
        "--show-safe-detail",
        action="store_true",
        help="include the provider's parsed, length-limited error message; avoid this in CI logs",
    )
    args = parser.parse_args()
    if not asyncio.run(_run(show_safe_detail=args.show_safe_detail)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
