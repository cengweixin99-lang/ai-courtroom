from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
import structlog

from mootcourt.agents.context_budget import ContextBudgetExceeded, estimate_text_tokens
from mootcourt.agents.prompt_builder import build_agent_prompt
from mootcourt.agents.provider_resilience import (
    ProviderResilience,
    ProviderResilienceError,
    enter_provider_call,
    leave_provider_call,
)
from mootcourt.agents.providers import (
    CONTROLLED_CITATION_PROTOCOL,
    AgentProviderRequest,
    AgentProviderResult,
    StructuredProviderRequest,
    TextUpdateCallback,
)
from mootcourt.core.observability import record_provider_guard_rejection, record_provider_retry

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _GeneratedBody:
    body: dict[str, Any]
    estimated_input_tokens: int
    request_count: int


class AgentProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_input_tokens: int = 0,
        provider_request_count: int = 0,
        estimated_cost_cny: float = 0,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.estimated_input_tokens = estimated_input_tokens
        self.provider_request_count = provider_request_count
        self.estimated_cost_cny = estimated_cost_cny
        self.http_status = http_status


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
        max_input_tokens: int = 24_000,
        max_retries: int = 2,
        max_incomplete_retries: int = 1,
        retry_base_delay_seconds: float = 0.5,
        response_format: Literal["json_schema", "json_object", "plain_json"] = "json_schema",
        max_tokens_field: Literal["max_completion_tokens", "max_tokens"] = (
            "max_completion_tokens"
        ),
        enable_thinking: bool | None = None,
        temperature: float = 0,
        input_cost_per_million_cny: float = 0,
        output_cost_per_million_cny: float = 0,
        transport: httpx.AsyncBaseTransport | None = None,
        resilience: ProviderResilience | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._max_input_tokens = max_input_tokens
        self._max_retries = max_retries
        self._max_incomplete_retries = max_incomplete_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._response_format = response_format
        self._max_tokens_field = max_tokens_field
        self._enable_thinking = enable_thinking
        self._temperature = temperature
        self._input_cost_per_million_cny = input_cost_per_million_cny
        self._output_cost_per_million_cny = output_cost_per_million_cny
        self._transport = transport
        self._resilience = resilience or ProviderResilience(
            max_concurrency=8,
            requests_per_second=0,
            queue_timeout_seconds=5,
            circuit_failure_threshold=5,
            circuit_recovery_seconds=30,
        )

    @property
    def provider_name(self) -> str:
        return "openai-compatible"

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        try:
            prompt = build_agent_prompt(
                request.context,
                request.instruction,
                request.repair_instruction,
                max_input_tokens=self._max_input_tokens,
            )
        except ContextBudgetExceeded as exc:
            raise AgentProviderError("agent_context_too_large", str(exc)) from exc
        if prompt.budget_report is not None:
            logger.info(
                "agent_context_trimmed",
                provider=self.provider_name,
                model=self.model_name,
                original_tokens=prompt.budget_report.original_tokens,
                final_tokens=prompt.budget_report.final_tokens,
                removed_event_count=prompt.budget_report.removed_event_count,
                removed_evidence_count=len(prompt.budget_report.removed_evidence_ids),
                removed_fact_count=len(prompt.budget_report.removed_fact_ids),
                removed_statement_count=len(prompt.budget_report.removed_statement_ids),
            )
        visible_field: Literal["speech", "answer"] = (
            "speech" if request.context.actor_role.value in {"prosecution", "defense"} else "answer"
        )
        return await self._generate_payload(
            messages=[
                {"role": item["role"], "content": item["content"]} for item in prompt.messages
            ],
            schema_name=prompt.schema_name,
            response_schema=prompt.response_schema,
            on_text_update=request.on_text_update,
            visible_field=visible_field,
        )

    async def generate_structured(self, request: StructuredProviderRequest) -> AgentProviderResult:
        """执行不属于庭审角色的严格 JSON 任务，例如教学质量评审。"""

        return await self._generate_payload(
            messages=list(request.messages),
            schema_name=request.schema_name,
            response_schema=request.response_schema,
            on_text_update=None,
            visible_field=None,
        )

    async def _generate_payload(
        self,
        *,
        messages: list[dict[str, str]],
        schema_name: str,
        response_schema: dict[str, Any],
        on_text_update: TextUpdateCallback | None,
        visible_field: Literal["speech", "answer"] | None,
    ) -> AgentProviderResult:
        try:
            await enter_provider_call()
        except ProviderResilienceError as exc:
            record_provider_guard_rejection(
                provider=self.provider_name,
                model=self.model_name,
                reason=exc.code,
            )
            logger.warning(
                "agent_provider_guard_rejected",
                provider=self.provider_name,
                model=self.model_name,
                reason=exc.code,
            )
            raise AgentProviderError(exc.code, exc.message) from exc
        try:
            try:
                await self._resilience.acquire()
            except ProviderResilienceError as exc:
                record_provider_guard_rejection(
                    provider=self.provider_name,
                    model=self.model_name,
                    reason=exc.code,
                )
                raise AgentProviderError(exc.code, exc.message) from exc
            try:
                result = await self._perform_generate_payload(
                    messages=messages,
                    schema_name=schema_name,
                    response_schema=response_schema,
                    on_text_update=on_text_update,
                    visible_field=visible_field,
                )
            except AgentProviderError as exc:
                if exc.code == "agent_provider_rate_limited":
                    await self._resilience.record_local_rejection()
                else:
                    await self._resilience.record_failure(transient=_is_transient_error(exc))
                raise
            else:
                await self._resilience.record_success()
                return result
            finally:
                await self._resilience.release_async()
        finally:
            await leave_provider_call()

    async def _perform_generate_payload(
        self,
        *,
        messages: list[dict[str, str]],
        schema_name: str,
        response_schema: dict[str, Any],
        on_text_update: TextUpdateCallback | None,
        visible_field: Literal["speech", "answer"] | None,
    ) -> AgentProviderResult:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            self._max_tokens_field: self._max_output_tokens,
            "temperature": self._temperature,
        }
        if self._enable_thinking is not None:
            # Qwen3/3.7 的隐藏思考会显著增加结构化任务延迟；兼容服务未配置时不发送该扩展字段。
            payload["enable_thinking"] = self._enable_thinking
        if self._response_format == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": response_schema,
                },
            }
        elif self._response_format == "json_object":
            # 部分兼容服务只接受 JSON Object 模式；输出仍由本地 Pydantic 严格校验。
            payload["response_format"] = {"type": "json_object"}
        estimated_tokens = _estimate_payload_input_tokens(payload)
        if estimated_tokens > self._max_input_tokens:
            raise AgentProviderError(
                "agent_context_too_large",
                f"model input requires approximately {estimated_tokens} tokens; "
                f"configured limit is {self._max_input_tokens}",
            )
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
            # 本地服务不经过系统代理，外部 Provider 继续遵循部署环境代理配置。
            trust_env=not _is_loopback_url(self._base_url),
        ) as client:
            generated = await self._generate_complete_body(
                client,
                headers,
                payload,
                on_text_update=on_text_update,
                visible_field=visible_field,
            )
        body = generated.body
        usage = body.get("usage")
        usage_object = usage if isinstance(usage, dict) else {}
        input_tokens = _non_negative_int(usage_object.get("prompt_tokens"))
        output_tokens = _non_negative_int(usage_object.get("completion_tokens"))
        estimated_cost = (
            input_tokens * self._input_cost_per_million_cny
            + output_tokens * self._output_cost_per_million_cny
        ) / 1_000_000
        try:
            output = _structured_output(body)
        except AgentProviderError as exc:
            # 上游已经完成计费时，即使结构化解析失败也必须把消耗传给 Trace 和会话预算。
            raise AgentProviderError(
                exc.code,
                exc.message,
                input_tokens=exc.input_tokens + input_tokens,
                output_tokens=exc.output_tokens + output_tokens,
                estimated_input_tokens=(
                    exc.estimated_input_tokens + generated.estimated_input_tokens
                ),
                provider_request_count=(exc.provider_request_count + generated.request_count),
                estimated_cost_cny=exc.estimated_cost_cny + estimated_cost,
                http_status=exc.http_status,
            ) from exc
        return AgentProviderResult(
            output=output,
            provider=self.provider_name,
            model=self.model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_input_tokens=generated.estimated_input_tokens,
            provider_request_count=generated.request_count,
            estimated_cost_cny=estimated_cost,
            citation_protocol=CONTROLLED_CITATION_PROTOCOL,
        )

    async def _generate_complete_body(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        payload: dict[str, Any],
        *,
        on_text_update: TextUpdateCallback | None,
        visible_field: Literal["speech", "answer"] | None,
    ) -> _GeneratedBody:
        """长度截断时重新生成完整对象，并累计所有实际模型调用的用量。"""
        total_input_tokens = 0
        total_output_tokens = 0
        total_estimated_input_tokens = 0
        request_count = 0
        body: dict[str, Any] = {}
        for incomplete_attempt in range(self._max_incomplete_retries + 1):
            attempt_payload = (
                payload if incomplete_attempt == 0 else _with_incomplete_retry_instruction(payload)
            )
            # 每次拿到模型 usage 的请求都单独计入估算，避免截断重生成被误判为 tokenizer 偏差。
            total_estimated_input_tokens += _estimate_payload_input_tokens(attempt_payload)
            request_count += 1
            try:
                if on_text_update is not None:
                    if visible_field is None:
                        raise RuntimeError("streamed structured request is missing a visible field")
                    body = await self._stream_with_retry(
                        client,
                        headers,
                        attempt_payload,
                        visible_field,
                        on_text_update,
                    )
                else:
                    body = await self._request_non_stream_body(client, headers, attempt_payload)
            except AgentProviderError as exc:
                # HTTP 拒绝同样是一次已发送的调用；保留计数以区分鉴权失败与未发起请求。
                raise AgentProviderError(
                    exc.code,
                    exc.message,
                    input_tokens=total_input_tokens + exc.input_tokens,
                    output_tokens=total_output_tokens + exc.output_tokens,
                    estimated_input_tokens=(
                        total_estimated_input_tokens + exc.estimated_input_tokens
                    ),
                    provider_request_count=request_count + exc.provider_request_count,
                    estimated_cost_cny=exc.estimated_cost_cny,
                    http_status=exc.http_status,
                ) from exc

            usage = body.get("usage")
            usage_object = usage if isinstance(usage, dict) else {}
            total_input_tokens += _non_negative_int(usage_object.get("prompt_tokens"))
            total_output_tokens += _non_negative_int(usage_object.get("completion_tokens"))
            if not _was_length_limited(body):
                break
            if incomplete_attempt < self._max_incomplete_retries:
                record_provider_retry(
                    provider=self.provider_name,
                    model=self.model_name,
                    reason="incomplete_output",
                )
                logger.warning(
                    "agent_provider_retrying",
                    provider=self.provider_name,
                    model=self.model_name,
                    reason="incomplete_output",
                    attempt=incomplete_attempt + 1,
                )
                # 半截 JSON 不能继续落库；清空预览后从头生成更精简的完整对象。
                if on_text_update is not None:
                    await on_text_update("")
                continue

        body["usage"] = {
            "prompt_tokens": total_input_tokens,
            "completion_tokens": total_output_tokens,
        }
        return _GeneratedBody(
            body=body,
            estimated_input_tokens=total_estimated_input_tokens,
            request_count=request_count,
        )

    async def _request_non_stream_body(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._post_with_retry(client, headers, payload)
        if _grammar_initialization_failed(response):
            # 部分兼容服务无法稳定编译复杂 JSON Schema；降级后仍执行本地严格校验。
            fallback_payload = dict(payload)
            fallback_payload.pop("response_format", None)
            response = await self._post_with_retry(client, headers, fallback_payload)
        if response.status_code >= 400:
            error_message = _safe_error_message(response)
            raise AgentProviderError(
                "agent_provider_http_error",
                f"model endpoint returned HTTP {response.status_code}: {error_message}",
                http_status=response.status_code,
            )
        return _response_object(response)

    async def _post_with_retry(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> httpx.Response:
        """仅重试瞬时网络故障和可恢复状态码，鉴权与参数错误立即返回。"""
        for attempt in range(self._max_retries + 1):
            try:
                await self._before_upstream_request()
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
            except httpx.TimeoutException as exc:
                if attempt >= self._max_retries:
                    raise AgentProviderError(
                        "agent_provider_timeout", "model request timed out"
                    ) from exc
                self._record_retry("timeout", attempt)
                await self._retry_delay(attempt)
                continue
            except httpx.HTTPError as exc:
                if attempt >= self._max_retries:
                    raise AgentProviderError(
                        "agent_provider_unavailable", "model endpoint is unavailable"
                    ) from exc
                self._record_retry("connection_error", attempt)
                await self._retry_delay(attempt)
                continue
            if response.status_code not in {408, 429} and response.status_code < 500:
                return response
            if attempt >= self._max_retries:
                return response
            self._record_retry(_retry_reason_for_status(response.status_code), attempt)
            await self._retry_delay(attempt)
        raise RuntimeError("model retry loop ended unexpectedly")

    async def _stream_with_retry(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        payload: dict[str, Any],
        visible_field: Literal["speech", "answer"],
        on_text_update: TextUpdateCallback,
    ) -> dict[str, Any]:
        stream_payload = {
            **payload,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        for attempt in range(self._max_retries + 1):
            content = ""
            last_preview = ""
            finish_reason: object = None
            usage: dict[str, Any] = {}
            try:
                await self._before_upstream_request()
                async with client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=stream_payload,
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        if _retryable_status(response.status_code) and attempt < self._max_retries:
                            self._record_retry(
                                _retry_reason_for_status(response.status_code), attempt
                            )
                            await on_text_update("")
                            await self._retry_delay(attempt)
                            continue
                        error_message = _safe_error_message(response)
                        raise AgentProviderError(
                            "agent_provider_http_error",
                            f"model endpoint returned HTTP {response.status_code}: {error_message}",
                            http_status=response.status_code,
                        )
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        chunk = _stream_chunk(data)
                        chunk_usage = chunk.get("usage")
                        if isinstance(chunk_usage, dict):
                            usage = chunk_usage
                        choices = chunk.get("choices")
                        if not isinstance(choices, list) or not choices:
                            continue
                        choice = choices[0]
                        if not isinstance(choice, dict):
                            continue
                        if choice.get("finish_reason") is not None:
                            finish_reason = choice["finish_reason"]
                        delta = choice.get("delta")
                        if not isinstance(delta, dict):
                            continue
                        # 禁止把模型内部推理 reasoning_content 暴露到庭审页面。
                        text_delta = delta.get("content")
                        if not isinstance(text_delta, str) or not text_delta:
                            continue
                        content += text_delta
                        preview = _visible_json_string(content, visible_field)
                        if preview != last_preview:
                            last_preview = preview
                            await on_text_update(preview)
                if not content:
                    raise AgentProviderError(
                        "agent_provider_invalid_response",
                        "streaming model response did not contain content",
                    )
                return {
                    "choices": [
                        {
                            "finish_reason": finish_reason,
                            "message": {"content": content},
                        }
                    ],
                    "usage": usage,
                }
            except AgentProviderError:
                raise
            except httpx.TimeoutException as exc:
                if attempt >= self._max_retries:
                    raise AgentProviderError(
                        "agent_provider_timeout", "model request timed out"
                    ) from exc
                self._record_retry("timeout", attempt)
            except httpx.HTTPError as exc:
                if attempt >= self._max_retries:
                    raise AgentProviderError(
                        "agent_provider_unavailable", "model endpoint is unavailable"
                    ) from exc
                self._record_retry("connection_error", attempt)
            await on_text_update("")
            await self._retry_delay(attempt)
        raise RuntimeError("streaming model retry loop ended unexpectedly")

    async def _retry_delay(self, attempt: int) -> None:
        delay = self._retry_base_delay_seconds * (2**attempt)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _before_upstream_request(self) -> None:
        try:
            await self._resilience.before_request()
        except ProviderResilienceError as exc:
            record_provider_guard_rejection(
                provider=self.provider_name,
                model=self.model_name,
                reason=exc.code,
            )
            logger.warning(
                "agent_provider_guard_rejected",
                provider=self.provider_name,
                model=self.model_name,
                reason=exc.code,
            )
            raise AgentProviderError(exc.code, exc.message) from exc

    def _record_retry(self, reason: str, attempt: int) -> None:
        record_provider_retry(
            provider=self.provider_name,
            model=self.model_name,
            reason=reason,
        )
        logger.warning(
            "agent_provider_retrying",
            provider=self.provider_name,
            model=self.model_name,
            reason=reason,
            attempt=attempt + 1,
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


def _is_transient_error(error: AgentProviderError) -> bool:
    if error.code in {"agent_provider_timeout", "agent_provider_unavailable"}:
        return True
    if error.code != "agent_provider_http_error":
        return False
    return any(f"HTTP {status}" in error.message for status in (408, 429, *range(500, 600)))


def _stream_chunk(data: str) -> dict[str, Any]:
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError as exc:
        raise AgentProviderError(
            "agent_provider_invalid_response", "model stream chunk is not valid JSON"
        ) from exc
    if not isinstance(chunk, dict):
        raise AgentProviderError(
            "agent_provider_invalid_response", "model stream chunk must be an object"
        )
    return chunk


def _visible_json_string(content: str, field: Literal["speech", "answer"]) -> str:
    """从尚未闭合的结构化 JSON 中解码可展示字符串，不读取其他字段。"""
    marker = f'"{field}"'
    marker_index = content.find(marker)
    if marker_index < 0:
        return ""
    colon_index = content.find(":", marker_index + len(marker))
    if colon_index < 0:
        return ""
    quote_index = colon_index + 1
    while quote_index < len(content) and content[quote_index].isspace():
        quote_index += 1
    if quote_index >= len(content) or content[quote_index] != '"':
        return ""
    encoded = content[quote_index + 1 :]
    escaped = False
    closing_index: int | None = None
    for index, char in enumerate(encoded):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            closing_index = index
            break
    if closing_index is not None:
        encoded = encoded[:closing_index]
    else:
        # 截断不完整的反斜杠或 unicode 转义，再交给标准 JSON 解码器处理。
        slash_index = encoded.rfind("\\")
        if slash_index >= 0:
            suffix = encoded[slash_index:]
            if suffix == "\\" or (suffix.startswith("\\u") and len(suffix) < 6):
                encoded = encoded[:slash_index]
    try:
        value = json.loads(f'"{encoded}"')
    except json.JSONDecodeError:
        return ""
    return value if isinstance(value, str) else ""


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
    # 部分 OpenAI-compatible 模型会把唯一结果包在数组中；只解包单个对象，
    # 多结果、空数组或非对象元素仍拒绝，后续继续执行完整 Schema 和业务权限校验。
    if isinstance(output, list) and len(output) == 1 and isinstance(output[0], dict):
        output = output[0]
    if not isinstance(output, dict):
        raise AgentProviderError(
            "agent_provider_invalid_response", "structured model output must be an object"
        )
    return output


def _was_length_limited(body: dict[str, Any]) -> bool:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False
    return choices[0].get("finish_reason") == "length"


def _with_incomplete_retry_instruction(payload: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages")
    retry_payload = dict(payload)
    if not isinstance(messages, list):
        return retry_payload
    retry_payload["messages"] = [
        *messages,
        {
            "role": "system",
            "content": (
                "上一次响应因达到长度上限而截断。请从头重新生成一个精简但完整的 JSON 对象；"
                "保留全部必填字段，压缩自然语言，不要续写半截 JSON，不要输出解释或 Markdown。"
            ),
        },
    ]
    return retry_payload


def _safe_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "non-JSON error response"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                # 只保留短错误信息，禁止把请求头、密钥或完整上游响应写入 Trace。
                return message[:500]
    return "unspecified provider error"


def _estimate_payload_input_tokens(payload: dict[str, Any]) -> int:
    """只估算会进入模型上下文的字段，避免把超时、温度等控制参数算作 Token。"""

    model_input: dict[str, Any] = {"messages": payload.get("messages", [])}
    if "response_format" in payload:
        model_input["response_format"] = payload["response_format"]
    return estimate_text_tokens(json.dumps(model_input, ensure_ascii=False, separators=(",", ":")))


def _non_negative_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _retryable_status(status_code: int) -> bool:
    return status_code in {408, 429} or status_code >= 500


def _retry_reason_for_status(status_code: int) -> str:
    if status_code == 408:
        return "http_408"
    if status_code == 429:
        return "http_429"
    return "http_5xx"


def _grammar_initialization_failed(response: httpx.Response) -> bool:
    return response.status_code == 400 and "failed to parse grammar" in response.text.lower()


def _is_loopback_url(value: str) -> bool:
    try:
        return (urlparse(value).hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
    except ValueError:
        return False
