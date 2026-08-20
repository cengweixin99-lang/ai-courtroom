from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from mootcourt.core.config import Settings
from mootcourt.search.embeddings import (
    CachedEmbeddingProvider,
    EmbeddingCacheStore,
    EmbeddingProviderError,
    OpenAICompatibleEmbeddingProvider,
    _is_loopback_url,
    build_embedding_provider,
)

MODEL_REGISTRY = "knowledge/legal/embedding_models.json"


class CountingEmbeddingProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    @property
    def model_name(self) -> str:
        return "counting-model"

    @property
    def version(self) -> str:
        return "v1"

    @property
    def dimensions(self) -> int:
        return 2

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(text)), 0.0] for text in texts]


async def test_cached_embedding_provider_reuses_vectors_per_text() -> None:
    inner = CountingEmbeddingProvider()
    provider = CachedEmbeddingProvider(inner, EmbeddingCacheStore(maxsize=8), ttl_seconds=3600)

    first = await provider.embed(["盗窃罪的构成要件"])
    second = await provider.embed(["盗窃罪的构成要件"])
    mixed = await provider.embed(["盗窃罪的构成要件", "排除合理怀疑"])

    assert first == second == [[8.0, 0.0]]
    assert mixed == [[8.0, 0.0], [6.0, 0.0]]
    # 三次调用只产生两次真实请求：首次 + 第二次混合中的新文本
    assert inner.calls == [["盗窃罪的构成要件"], ["排除合理怀疑"]]


async def test_cached_embedding_provider_refreshes_expired_entries() -> None:
    inner = CountingEmbeddingProvider()
    store = EmbeddingCacheStore(maxsize=8)
    provider = CachedEmbeddingProvider(inner, store, ttl_seconds=30)

    await provider.embed(["盗窃"])
    # 把条目时间戳拨到过去，模拟 TTL 过期
    key = next(iter(store._entries))
    _, vector = store._entries[key]
    store._entries[key] = (0.0, vector)

    await provider.embed(["盗窃"])

    assert inner.calls == [["盗窃"], ["盗窃"]]


async def test_embedding_provider_sends_versioned_dimensions_and_orders_results() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                    {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                ]
            },
        )

    provider = OpenAICompatibleEmbeddingProvider(
        api_key="test-secret",
        model="legal-embedding-test",
        version="legal-embedding-test-v1",
        dimensions=3,
        base_url="https://example.test/v1/",
        transport=httpx.MockTransport(handler),
    )

    vectors = await provider.embed(["第一条", "第二条"])

    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert captured["authorization"] == "Bearer test-secret"
    assert captured["payload"] == {
        "model": "legal-embedding-test",
        "input": ["第一条", "第二条"],
        "dimensions": 3,
        "encoding_format": "float",
    }


def test_only_loopback_embedding_urls_bypass_environment_proxy() -> None:
    assert _is_loopback_url("http://127.0.0.1:11434/v1")
    assert _is_loopback_url("http://localhost:11434/v1")
    assert _is_loopback_url("http://[::1]:11434/v1")
    assert not _is_loopback_url("https://api.openai.com/v1")


async def test_embedding_provider_rejects_wrong_dimensions() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    provider = OpenAICompatibleEmbeddingProvider(
        api_key="test-key",
        model="test-model",
        version="test-v1",
        dimensions=3,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(EmbeddingProviderError, match="dimensions"):
        await provider.embed(["法律问题"])


def test_embedding_provider_is_disabled_by_default() -> None:
    assert build_embedding_provider(Settings()) is None


def test_embedding_provider_requires_explicit_version() -> None:
    with pytest.raises(EmbeddingProviderError, match="non-disabled version"):
        build_embedding_provider(
            Settings(
                legal_embedding_enabled=True,
                legal_embedding_model="test-model",
                legal_embedding_api_key=SecretStr("test-key"),
            )
        )


def test_candidate_embedding_is_allowed_only_for_offline_eval() -> None:
    settings = Settings(
        legal_embedding_enabled=True,
        legal_embedding_registry_path=MODEL_REGISTRY,
        legal_embedding_provider="openai-compatible",
        legal_embedding_model="bge-m3",
        legal_embedding_api_key=SecretStr("test-key"),
        legal_embedding_version="ollama-bge-m3-790764642607-1024-v1",
        legal_embedding_dimensions=1024,
    )

    with pytest.raises(EmbeddingProviderError, match="not enabled for runtime"):
        build_embedding_provider(settings)

    provider = build_embedding_provider(settings, allow_candidate=True)
    assert provider is not None
    assert provider.model_name == "bge-m3"
    assert provider.version == "ollama-bge-m3-790764642607-1024-v1"


def test_embedding_configuration_must_match_registry() -> None:
    settings = Settings(
        legal_embedding_enabled=True,
        legal_embedding_registry_path=MODEL_REGISTRY,
        legal_embedding_provider="openai-compatible",
        legal_embedding_model="bge-m3",
        legal_embedding_api_key=SecretStr("test-key"),
        legal_embedding_version="bge-m3-unreviewed-v2",
        legal_embedding_dimensions=1024,
    )

    with pytest.raises(EmbeddingProviderError, match="exactly match"):
        build_embedding_provider(settings, allow_candidate=True)
