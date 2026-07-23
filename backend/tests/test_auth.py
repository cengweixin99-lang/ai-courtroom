from __future__ import annotations

import base64
import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
from pydantic import SecretStr, ValidationError

from mootcourt.core import auth
from mootcourt.core.auth import AuthenticationError, authenticate_bearer_token
from mootcourt.core.config import Settings


async def test_development_bypass_requires_an_explicit_switch() -> None:
    settings = Settings(auth_dev_bypass_enabled=True, auth_dev_subject="test-subject")

    principal = await authenticate_bearer_token(None, settings)

    assert principal.subject == "test-subject"
    assert principal.development_bypass is True


async def test_missing_token_is_rejected_without_development_bypass() -> None:
    with pytest.raises(AuthenticationError, match="missing bearer token"):
        await authenticate_bearer_token(None, Settings(auth_dev_bypass_enabled=False))


async def test_valid_rs256_token_is_authenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    jwk = {
        "kid": "rsa-key",
        "kty": "RSA",
        "n": _b64_int(public_numbers.n),
        "e": _b64_int(public_numbers.e),
    }
    settings = _auth_settings()
    token = _signed_token(
        private_key,
        "RS256",
        "rsa-key",
        _valid_claims(settings),
    )
    load_keys = AsyncMock(return_value={"rsa-key": jwk})
    monkeypatch.setattr(auth, "_get_jwks", load_keys)

    principal = await authenticate_bearer_token(token, settings)

    assert principal.subject == "user-123"
    assert principal.email == "user@example.test"
    assert principal.provider_role == "authenticated"
    assert load_keys.await_count == 1


async def test_valid_es256_token_is_authenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_numbers = private_key.public_key().public_numbers()
    jwk = {
        "kid": "ec-key",
        "kty": "EC",
        "crv": "P-256",
        "x": _b64_int(public_numbers.x),
        "y": _b64_int(public_numbers.y),
    }
    settings = _auth_settings()
    token = _signed_token(private_key, "ES256", "ec-key", _valid_claims(settings))
    monkeypatch.setattr(auth, "_get_jwks", AsyncMock(return_value={"ec-key": jwk}))

    principal = await authenticate_bearer_token(token, settings)

    assert principal.subject == "user-123"


async def test_unknown_signing_key_forces_one_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _auth_settings()
    token = _unsigned_token("RS256", "rotated-key", _valid_claims(settings))
    load_keys = AsyncMock(side_effect=[{"old-key": {}}, {"new-key": {}}])
    monkeypatch.setattr(auth, "_get_jwks", load_keys)

    with pytest.raises(AuthenticationError, match="signing key is unknown"):
        await authenticate_bearer_token(token, settings)

    assert load_keys.await_count == 2
    assert load_keys.await_args_list[1].kwargs == {"force_refresh": True}


@pytest.mark.parametrize(
    ("claims_patch", "message"),
    [
        ({"iss": "https://wrong.example/auth/v1"}, "issuer is invalid"),
        ({"aud": "wrong-audience"}, "audience is invalid"),
        ({"exp": 0}, "token is expired"),
        ({"nbf": time.time() + 120}, "not active yet"),
    ],
)
def test_claim_validation_rejects_untrusted_claims(
    claims_patch: dict[str, object], message: str
) -> None:
    settings = _auth_settings()
    claims = _valid_claims(settings) | claims_patch

    with pytest.raises(AuthenticationError, match=message):
        auth._validate_claims(claims, settings)


@pytest.mark.parametrize("token", ["one.two", "!!!.e30.", "W10.W10."])
def test_malformed_token_is_rejected(token: str) -> None:
    with pytest.raises(AuthenticationError):
        auth._parse_compact_jwt(token)


async def test_jwks_are_loaded_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert request.url.path.endswith("/auth/v1/.well-known/jwks.json")
        return httpx.Response(200, json={"keys": [{"kid": "cached-key", "kty": "RSA"}]})

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        auth.httpx,
        "AsyncClient",
        lambda **_kwargs: original_client(transport=transport),
    )
    settings = _auth_settings(supabase_url="https://cache-test.supabase.co")
    auth._jwks_cache.clear()

    first = await auth._get_jwks(settings)
    second = await auth._get_jwks(settings)

    assert first == second == {"cached-key": {"kid": "cached-key", "kty": "RSA"}}
    assert requests == 1


@pytest.mark.parametrize(
    "payload",
    [[], {}, {"keys": []}, {"keys": [{"kty": "RSA"}]}],
)
async def test_invalid_jwks_payload_is_rejected(
    monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    monkeypatch.setattr(
        auth.httpx,
        "AsyncClient",
        lambda **_kwargs: original_client(transport=transport),
    )
    settings = _auth_settings(supabase_url=f"https://invalid-{id(payload)}.supabase.co")

    with pytest.raises(AuthenticationError, match="signing keys"):
        await auth._get_jwks(settings, force_refresh=True)


def test_production_rejects_missing_supabase_configuration() -> None:
    with pytest.raises(ValidationError, match="SUPABASE_URL"):
        Settings(
            app_env="production",
            diagnostics_api_key=SecretStr("a" * 32),
            trace_redaction_hmac_key=SecretStr("b" * 32),
            # 明确覆盖开发机 .env，确保这里验证的是生产环境缺少认证配置的行为。
            supabase_url="",
            supabase_jwt_issuer="",
        )


def test_production_rejects_development_bypass() -> None:
    with pytest.raises(ValidationError, match="AUTH_DEV_BYPASS_ENABLED"):
        Settings(
            app_env="production",
            diagnostics_api_key=SecretStr("a" * 32),
            trace_redaction_hmac_key=SecretStr("b" * 32),
            supabase_url="https://example.supabase.co",
            supabase_jwt_issuer="https://example.supabase.co/auth/v1",
            auth_dev_bypass_enabled=True,
        )


def _auth_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "supabase_url": "https://example.supabase.co",
        "supabase_jwt_issuer": "https://example.supabase.co/auth/v1",
        "supabase_jwt_audience": "authenticated",
        **overrides,
    }
    return Settings(**values)


def _valid_claims(settings: Settings) -> dict[str, object]:
    return {
        "sub": "user-123",
        "email": "user@example.test",
        "role": "authenticated",
        "iss": settings.supabase_jwt_issuer,
        "aud": ["authenticated", "another-audience"],
        "exp": time.time() + 300,
        "nbf": time.time() - 10,
    }


def _signed_token(
    private_key: rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey,
    algorithm: str,
    key_id: str,
    claims: dict[str, object],
) -> str:
    unsigned = _unsigned_token(algorithm, key_id, claims, include_signature=False)
    signed_data = unsigned.removesuffix(".").encode()
    if isinstance(private_key, rsa.RSAPrivateKey):
        signature = private_key.sign(signed_data, padding.PKCS1v15(), hashes.SHA256())
    else:
        der_signature = private_key.sign(signed_data, ec.ECDSA(hashes.SHA256()))
        r_value, s_value = utils.decode_dss_signature(der_signature)
        signature = r_value.to_bytes(32, "big") + s_value.to_bytes(32, "big")
    return f"{signed_data.decode()}.{_b64(signature)}"


def _unsigned_token(
    algorithm: str,
    key_id: str,
    claims: dict[str, object],
    *,
    include_signature: bool = True,
) -> str:
    header = _b64(json.dumps({"alg": algorithm, "kid": key_id}).encode())
    payload = _b64(json.dumps(claims).encode())
    signature = _b64(b"unsigned") if include_signature else ""
    return f"{header}.{payload}.{signature}"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _b64_int(value: int) -> str:
    size = max(1, (value.bit_length() + 7) // 8)
    return _b64(value.to_bytes(size, "big"))
