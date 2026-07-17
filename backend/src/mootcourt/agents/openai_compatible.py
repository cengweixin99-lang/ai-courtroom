from __future__ import annotations

import json
from typing import Any

import httpx

from mootcourt.agents.prompt_builder import build_agent_prompt
from mootcourt.agents.providers import AgentProviderRequest, AgentProviderResult


class AgentProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class OpenAICompatibleProvider:
    """通过 Chat Completions 严格结构化输出协议接入 OpenAI-compatible 服务。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30,
        max_output_tokens: int = 2_000,
        input_cost_per_million_cny: float = 0,
        output_cost_per_million_cny: float = 0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._input_cost_per_million_cny = input_cost_per_million_cny
        self._output_cost_per_million_cny = output_cost_per_million_cny
        self._transport = transport

    @property
    def provider_name(self) -> str:
        return "openai-compatible"

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        prompt = build_agent_prompt(
            request.context,
            request.instruction,
            request.repair_instruction,
        )
        payload = {
            "model": self._model,
            "messages": list(prompt.messages),
            "max_completion_tokens": self._max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": prompt.schema_name,
                    "strict": True,
                    "schema": prompt.response_schema,
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise AgentProviderError("agent_provider_timeout", "model request timed out") from exc
        except httpx.HTTPError as exc:
            raise AgentProviderError(
                "agent_provider_unavailable", "model endpoint is unavailable"
            ) from exc

        if response.status_code >= 400:
            error_message = _safe_error_message(response)
            raise AgentProviderError(
                "agent_provider_http_error",
                f"model endpoint returned HTTP {response.status_code}: {error_message}",
            )
        body = _response_object(response)
        output = _structured_output(body)
        usage = body.get("usage")
        usage_object = usage if isinstance(usage, dict) else {}
        input_tokens = _non_negative_int(usage_object.get("prompt_tokens"))
        output_tokens = _non_negative_int(usage_object.get("completion_tokens"))
        estimated_cost = (
            input_tokens * self._input_cost_per_million_cny
            + output_tokens * self._output_cost_per_million_cny
        ) / 1_000_000
        return AgentProviderResult(
            output=output,
            provider=self.provider_name,
            model=self.model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_cny=estimated_cost,
        )


def _response_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise AgentProviderError(
            "agent_provider_invalid_response", "model response is not JSON"
        ) from exc
    if not isinstance(body, dict):
        raise AgentProviderError(
            "agent_provider_invalid_response", "model response root must be an object"
        )
    return body


def _structured_output(body: dict[str, Any]) -> dict[str, Any]:
    try:
        choice = body["choices"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise AgentProviderError(
            "agent_provider_invalid_response", "model response is missing choices[0]"
        ) from exc
    if not isinstance(choice, dict):
        raise AgentProviderError(
            "agent_provider_invalid_response", "model choice must be an object"
        )
    finish_reason = choice.get("finish_reason")
    if finish_reason not in {None, "stop"}:
        # 长度截断或内容过滤后的 JSON 即使能解析，也不能视为完整业务输出。
        raise AgentProviderError(
            "agent_provider_incomplete",
            f"model response ended with finish_reason={finish_reason}",
        )
    message = choice.get("message")
    if not isinstance(message, dict):
        raise AgentProviderError(
            "agent_provider_invalid_response", "model response is missing a message object"
        )
    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal:
        raise AgentProviderError("agent_provider_refused", "model refused structured output")
    content = message.get("content")
    if not isinstance(content, str) or not content:
        raise AgentProviderError(
            "agent_provider_invalid_response", "model message content must be JSON text"
        )
    try:
        output = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AgentProviderError(
            "agent_provider_invalid_response", "model message content is not valid JSON"
        ) from exc
    if not isinstance(output, dict):
        raise AgentProviderError(
            "agent_provider_invalid_response", "structured model output must be an object"
        )
    return output


def _safe_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "non-JSON error response"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            # 只保留短错误信息，禁止把请求头、密钥或完整上游响应写入 Trace。
            return error["message"][:500]
    return "unspecified provider error"


def _non_negative_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
