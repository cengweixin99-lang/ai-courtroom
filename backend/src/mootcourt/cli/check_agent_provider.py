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


async def _run() -> bool:
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
            print(
                json.dumps(
                    {
                        "passed": False,
                        "code": exc.code,
                        "http_status": exc.http_status,
                        "provider_request_count": exc.provider_request_count,
                    }
                )
            )
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
    parser.parse_args()
    if not asyncio.run(_run()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
