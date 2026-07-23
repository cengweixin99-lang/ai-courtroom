from __future__ import annotations

import json

import pytest

from mootcourt.agents.openai_compatible import AgentProviderError
from mootcourt.cli.check_agent_provider import _run


class FailingStructuredProvider:
    async def generate_structured(self, _: object) -> object:
        raise AgentProviderError(
            "agent_provider_http_error",
            "model endpoint returned HTTP 403: private upstream detail",
            estimated_input_tokens=42,
            provider_request_count=1,
            http_status=403,
        )


async def test_provider_preflight_exposes_only_safe_failure_diagnostics(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def dispose() -> None:
        return None

    monkeypatch.setattr(
        "mootcourt.cli.check_agent_provider.build_agent_provider",
        lambda _settings, *, allow_fake: FailingStructuredProvider(),
    )
    monkeypatch.setattr("mootcourt.cli.check_agent_provider.dispose_redis", dispose)

    assert await _run() is False

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "passed": False,
        "code": "agent_provider_http_error",
        "http_status": 403,
        "provider_request_count": 1,
    }
