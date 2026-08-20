from __future__ import annotations

import math
from collections import OrderedDict
from pathlib import Path
from time import monotonic
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from mootcourt.core.config import Settings
from mootcourt.schemas.eval.embedding_models import load_embedding_model_registry


class EmbeddingProviderError(Exception):
    pass


class EmbeddingProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingCacheStore:
    """跨请求共享的 query embedding 存储；键含模型与版本，模型升级后旧条目自然失效。"""

    def __init__(self, *, maxsize: int) -> None:
        self.maxsize = maxsize
        self._entries: OrderedDict[tuple[str, str, str], tuple[float, list[float]]] = OrderedDict()

    def get(self, key: tuple[str, str, str], now: float, ttl_seconds: float) -> list[float] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        created_at, vector = entry
        if now - created_at >= ttl_seconds:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return vector

    def put(self, key: tuple[str, str, str], now: float, vector: list[float]) -> None:
        self._entries[key] = (now, vector)
        self._entries.move_to_end(key)
        while len(self._entries) > self.maxsize:
            self._entries.popitem(last=False)


class CachedEmbeddingProvider:
    """按文本缓存 embedding，前端固定法律问题等重复查询不再调用外部 API。"""

    def __init__(
        self,
        inner: EmbeddingProvider,
        store: EmbeddingCacheStore,
        *,
        ttl_seconds: float,
    ) -> None:
        self._inner = inner
        self._store = store
        self._ttl_seconds = ttl_seconds

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    @property
    def version(self) -> str:
        return self._inner.version

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        now = monotonic()
        keys = [(self.model_name, self.version, text) for text in texts]
        results: list[list[float] | None] = [
            self._store.get(key, now, self._ttl_seconds) for key in keys
        ]
        missing_indexes = [index for index, vector in enumerate(results) if vector is None]
        if missing_indexes:
            fetched = await self._inner.embed([texts[index] for index in missing_indexes])
            if len(fetched) != len(missing_indexes):
                raise EmbeddingProviderError("embedding provider must return one vector per text")
            now = monotonic()
            for index, vector in zip(missing_indexes, fetched, strict=True):
                self._store.put(keys[index], now, vector)
                results[index] = vector
        return [vector for vector in results if vector is not None]


class OpenAICompatibleEmbeddingProvider:
    """通过 OpenAI-compatible Embeddings API 生成法律文本向量。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        version: str,
        dimensions: int,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._version = version
        self._dimensions = dimensions
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def version(self) -> str:
        return self._version

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {
            "model": self._model,
            "input": texts,
            "dimensions": self._dimensions,
            "encoding_format": "float",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
                # 本地 Ollama 不应经过系统代理；外部 Provider 继续遵循部署环境代理配置。
                trust_env=not _is_loopback_url(self._base_url),
            ) as client:
                response = await client.post(
                    f"{self._base_url}/embeddings",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise EmbeddingProviderError("embedding request timed out") from exc
        except httpx.HTTPError as exc:
            raise EmbeddingProviderError("embedding endpoint is unavailable") from exc
        if response.status_code >= 400:
            raise EmbeddingProviderError(
                f"embedding endpoint returned HTTP {response.status_code}: "
                f"{_safe_error_message(response)}"
            )
        return _parse_embeddings(response, len(texts), self._dimensions)


def build_embedding_provider(
    settings: Settings, *, allow_candidate: bool = False
) -> EmbeddingProvider | None:
    if not settings.legal_embedding_enabled:
        return None
    if settings.legal_embedding_provider not in {"openai", "openai-compatible"}:
        raise EmbeddingProviderError(
            f"unsupported embedding provider: {settings.legal_embedding_provider}"
        )
    api_key = settings.legal_embedding_api_key.get_secret_value()
    if (
        not settings.legal_embedding_model
        or not api_key
        or not settings.legal_embedding_version
        or settings.legal_embedding_version == "disabled"
    ):
        raise EmbeddingProviderError(
            "embedding model, key, and a non-disabled version are required"
        )
    try:
        registry = load_embedding_model_registry(
            _resolve_registry_path(settings.legal_embedding_registry_path)
        )
        profile = registry.require_configured_model(settings)
        if not allow_candidate and not profile.enabled_for_runtime:
            raise ValueError("configured embedding model is not enabled for runtime")
    except ValueError as exc:
        raise EmbeddingProviderError(str(exc)) from exc
    return OpenAICompatibleEmbeddingProvider(
        api_key=api_key,
        model=settings.legal_embedding_model,
        version=settings.legal_embedding_version,
        dimensions=settings.legal_embedding_dimensions,
        base_url=settings.legal_embedding_base_url or "https://api.openai.com/v1",
        timeout_seconds=settings.legal_embedding_timeout_seconds,
    )


def _resolve_registry_path(configured_path: str) -> Path:
    path = Path(configured_path)
    if path.is_absolute() or path.exists():
        return path
    repository_relative = Path(__file__).resolve().parents[4] / path
    if repository_relative.exists():
        return repository_relative
    backend_relative = Path(__file__).resolve().parents[3] / path
    return backend_relative


def _parse_embeddings(
    response: httpx.Response, expected_count: int, dimensions: int
) -> list[list[float]]:
    try:
        body = response.json()
    except ValueError as exc:
        raise EmbeddingProviderError("embedding response is not JSON") from exc
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or len(data) != expected_count:
        raise EmbeddingProviderError("embedding response count does not match input count")
    ordered: list[list[float] | None] = [None] * expected_count
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("index"), int):
            raise EmbeddingProviderError("embedding response item is invalid")
        index = item["index"]
        raw_vector = item.get("embedding")
        if index < 0 or index >= expected_count or not isinstance(raw_vector, list):
            raise EmbeddingProviderError("embedding response index or vector is invalid")
        vector = [float(value) for value in raw_vector]
        if len(vector) != dimensions or not all(math.isfinite(value) for value in vector):
            raise EmbeddingProviderError("embedding dimensions or values are invalid")
        if ordered[index] is not None:
            raise EmbeddingProviderError("embedding response contains duplicate indexes")
        ordered[index] = vector
    if any(vector is None for vector in ordered):
        raise EmbeddingProviderError("embedding response is missing an index")
    return [vector for vector in ordered if vector is not None]


def _safe_error_message(response: httpx.Response) -> str:
    try:
        body: Any = response.json()
    except ValueError:
        return "non-JSON error response"
    if isinstance(body, dict):
        error = body.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        if isinstance(message, str):
            # 仅保留上游短错误，不记录请求内容、密钥或完整响应。
            return message[:500]
    return "unspecified provider error"


def _is_loopback_url(url: str) -> bool:
    return urlparse(url).hostname in {"localhost", "127.0.0.1", "::1"}
