from __future__ import annotations

from mootcourt.agents.openai_compatible import OpenAICompatibleProvider
from mootcourt.agents.providers import AgentProvider, FakeAgentProvider
from mootcourt.core.config import Settings


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
    return OpenAICompatibleProvider(
        api_key=api_key,
        model=settings.llm_model,
        base_url=settings.llm_base_url or "https://api.openai.com/v1",
        timeout_seconds=settings.llm_timeout_seconds,
        max_output_tokens=settings.llm_max_output_tokens,
        max_retries=settings.llm_max_retries,
        max_incomplete_retries=settings.llm_max_incomplete_retries,
        retry_base_delay_seconds=settings.llm_retry_base_delay_seconds,
        response_format=settings.llm_response_format,
        max_tokens_field=settings.llm_max_tokens_field,
        enable_thinking=enable_thinking,
        temperature=settings.llm_temperature,
        input_cost_per_million_cny=settings.llm_input_cost_per_million_cny,
        output_cost_per_million_cny=settings.llm_output_cost_per_million_cny,
    )
