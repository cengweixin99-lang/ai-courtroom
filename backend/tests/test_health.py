from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient
from prometheus_client.parser import text_string_to_metric_families
from pydantic import SecretStr, ValidationError

from mootcourt.api.dependencies import get_database_health_probe, get_search_health_probe
from mootcourt.core.config import Settings
from mootcourt.core.redis import dispose_redis, get_redis_client
from mootcourt.main import app
from mootcourt.repositories.health import ElasticsearchHealthRepository, RedisHealthRepository
from mootcourt.services.health import check_readiness


class StubHealthProbe:
    def __init__(self, healthy: bool) -> None:
        self._healthy = healthy

    async def ping(self) -> bool:
        return self._healthy


class StubElasticsearchClient:
    def __init__(self) -> None:
        self.options_kwargs: dict[str, object] | None = None

    def options(self, **kwargs: object) -> "StubElasticsearchClient":
        self.options_kwargs = kwargs
        return self

    async def ping(self) -> bool:
        return True


async def test_health_endpoint() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "mootcourt-api"
    assert response.headers["x-request-id"]


async def test_request_id_is_echoed_and_invalid_value_is_replaced() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        accepted = await client.get(
            "/api/v1/health", headers={"X-Request-ID": "frontend-request-001"}
        )
        replaced = await client.get("/api/v1/health", headers={"X-Request-ID": "bad value"})

    assert accepted.headers["x-request-id"] == "frontend-request-001"
    assert replaced.headers["x-request-id"] != "bad value"
    assert len(replaced.headers["x-request-id"]) == 36


async def test_metrics_endpoint_exposes_templated_http_metrics() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/api/v1/health")
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    samples = [
        sample
        for family in text_string_to_metric_families(response.text)
        for sample in family.samples
    ]
    assert any(
        sample.name == "mootcourt_http_requests_total"
        and sample.labels
        == {
            "method": "GET",
            "route": "/api/v1/health",
            "status_code": "200",
        }
        for sample in samples
    )


async def test_metrics_requires_diagnostics_key_in_production(monkeypatch: Any) -> None:
    settings = Settings(
        app_env="production",
        diagnostics_api_key=SecretStr("d" * 32),
        trace_redaction_hmac_key=SecretStr("h" * 32),
        supabase_url="https://example.supabase.co",
        supabase_jwt_issuer="https://example.supabase.co/auth/v1",
    )
    monkeypatch.setattr("mootcourt.core.observability.get_settings", lambda: settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get("/metrics")
        allowed = await client.get("/metrics", headers={"X-Diagnostics-Key": "d" * 32})

    assert denied.status_code == 401
    assert denied.json()["detail"]["code"] == "diagnostics_auth_required"
    assert allowed.status_code == 200


async def test_readiness_reports_dependency_state_without_error_details() -> None:
    app.dependency_overrides[get_database_health_probe] = lambda: StubHealthProbe(True)
    app.dependency_overrides[get_search_health_probe] = lambda: StubHealthProbe(False)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/ready")
    finally:
        app.dependency_overrides.pop(get_database_health_probe, None)
        app.dependency_overrides.pop(get_search_health_probe, None)

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["components"]["database"]["status"] == "ok"
    assert response.json()["components"]["elasticsearch"]["status"] == "unavailable"
    assert "error" not in response.json()["components"]["elasticsearch"]


async def test_elasticsearch_readiness_probe_disables_transport_retries() -> None:
    client = StubElasticsearchClient()
    repository = ElasticsearchHealthRepository(cast(Any, client))

    assert await repository.ping() is True
    assert client.options_kwargs == {"max_retries": 0}


async def test_readiness_includes_configured_optional_redis_probe() -> None:
    result = await check_readiness(
        StubHealthProbe(True),
        StubHealthProbe(True),
        1,
        {"redis": StubHealthProbe(False)},
    )
    assert result.status == "not_ready"
    assert result.components["redis"].status == "unavailable"


async def test_redis_health_repository_pings_client() -> None:
    repository = RedisHealthRepository(StubHealthProbe(True))
    assert await repository.ping() is True


async def test_redis_client_lifecycle_does_not_connect_eagerly() -> None:
    client = get_redis_client("redis://localhost:6379/15")
    assert client is get_redis_client("redis://localhost:6379/15")
    await dispose_redis()


def test_redis_url_rejects_non_redis_scheme() -> None:
    with pytest.raises(ValidationError):
        Settings(redis_url="http://localhost:6379")
