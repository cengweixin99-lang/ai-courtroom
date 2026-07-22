from __future__ import annotations

import hashlib

from mootcourt.agents.openai_compatible import OpenAICompatibleProvider
from mootcourt.agents.provider_resilience import shared_provider_resilience
from mootcourt.agents.providers import AgentProvider, FakeAgentProvider
from mootcourt.core.config import Settings
from mootcourt.core.redis import get_redis_client


class AgentProviderConfigurationError(ValueError):
    """运行时 Agent Provider 配置不完整或不受支持。"""


def build_agent_provider(settings: Settings, *, allow_fake: bool = False) -> AgentProvider:
    if settings.llm_provider == "fake":
        if not allow_fake:
            raise AgentProviderConfigurationError(
                "real Qwen Agent Eval requires LLM_PROVIDER=openai-compatible"
            )
        return FakeAgentProvider()
    if settings.llm_provider not in {"openai", "openai-compatible"}:
        raise AgentProviderConfigurationError(
            f"unsupported LLM_PROVIDER: {settings.llm_provider}"
        )
    if not settings.llm_model:
        raise AgentProviderConfigurationError("LLM_MODEL is required")
    api_key = settings.llm_api_key.get_secret_value()
    if not api_key:
        raise AgentProviderConfigurationError("LLM_API_KEY is required")
    enable_thinking = settings.llm_enable_thinking
    if enable_thinking is None and "qwen" in settings.llm_model.lower():
        # 庭审 Agent 需要低延迟、可校验 JSON，而不是不可见的长链推理。
        enable_thinking = False
    redis_identity = (
        hashlib.sha256(settings.redis_url.encode("utf-8")).hexdigest()[:12]
        if settings.redis_url
        else "local"
    )
    resilience = shared_provider_resilience(
        f"{redis_identity}|{settings.llm_base_url}|{settings.llm_model}",
        redis_client=(get_redis_client(settings.redis_url) if settings.redis_url else None),
        redis_key_prefix=settings.redis_key_prefix,
        distributed_lease_seconds=settings.agent_invocation_lease_seconds,
        max_concurrency=settings.llm_max_concurrency,
        requests_per_second=settings.llm_requests_per_second,
        queue_timeout_seconds=settings.llm_queue_timeout_seconds,
        circuit_failure_threshold=settings.llm_circuit_failure_threshold,
        circuit_recovery_seconds=settings.llm_circuit_recovery_seconds,
    )
    return OpenAICompatibleProvider(
        api_key=api_key,
        model=settings.llm_model,
        base_url=settings.llm_base_url or "https://api.openai.com/v1",
        timeout_seconds=settings.llm_timeout_seconds,
        max_output_tokens=settings.llm_max_output_tokens,
        max_input_tokens=settings.llm_max_input_tokens,
        max_retries=settings.llm_max_retries,
        max_incomplete_retries=settings.llm_max_incomplete_retries,
        retry_base_delay_seconds=settings.llm_retry_base_delay_seconds,
        response_format=settings.llm_response_format,
        max_tokens_field=settings.llm_max_tokens_field,
        enable_thinking=enable_thinking,
        temperature=settings.llm_temperature,
        input_cost_per_million_cny=settings.llm_input_cost_per_million_cny,
        output_cost_per_million_cny=settings.llm_output_cost_per_million_cny,
        resilience=resilience,
    )
