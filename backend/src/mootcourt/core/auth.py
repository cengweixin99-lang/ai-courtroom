from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils

from mootcourt.core.config import Settings


class AuthenticationError(Exception):
    """Raised when an access token cannot be trusted."""


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    subject: str
    email: str | None
    provider_role: str
    claims: dict[str, Any]
    development_bypass: bool = False


_jwks_cache: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}
_jwks_cache_lock = Lock()


async def authenticate_bearer_token(
    token: str | None, settings: Settings
) -> AuthenticatedPrincipal:
    """Validate a Supabase access token using its public JWKS document.

    The backend only consumes public signing keys.  No service-role secret is needed
    or accepted for this authentication flow.
    """
    if settings.auth_dev_bypass_enabled and not token:
        return AuthenticatedPrincipal(
            subject=settings.auth_dev_subject,
            email=settings.auth_dev_email,
            provider_role="authenticated",
            claims={"sub": settings.auth_dev_subject, "email": settings.auth_dev_email},
            development_bypass=True,
        )
    if not token:
        raise AuthenticationError("missing bearer token")
    if not settings.supabase_url or not settings.supabase_jwt_issuer:
        raise AuthenticationError("Supabase authentication is not configured")

    header, claims, signed_data, signature = _parse_compact_jwt(token)
    algorithm = header.get("alg")
    key_id = header.get("kid")
    if algorithm not in {"RS256", "ES256"} or not isinstance(key_id, str):
        raise AuthenticationError("unsupported signing algorithm or missing key id")
    keys = await _get_jwks(settings)
    key = keys.get(key_id)
    if key is None:
        # Signing-key rotation should take effect immediately instead of waiting for
        # the ordinary cache period.
        keys = await _get_jwks(settings, force_refresh=True)
        key = keys.get(key_id)
    if key is None:
        raise AuthenticationError("token signing key is unknown")
    _verify_signature(algorithm, key, signed_data, signature)
    _validate_claims(claims, settings)
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthenticationError("token subject is missing")
    email = claims.get("email")
    return AuthenticatedPrincipal(
        subject=subject,
        email=email if isinstance(email, str) else None,
        provider_role=str(claims.get("role", "authenticated")),
        claims=claims,
    )


def _parse_compact_jwt(token: str) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthenticationError("token format is invalid")
    try:
        header = json.loads(_base64url_decode(parts[0]))
        claims = json.loads(_base64url_decode(parts[1]))
        signature = _base64url_decode(parts[2])
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise AuthenticationError("token encoding is invalid") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise AuthenticationError("token payload is invalid")
    return header, claims, f"{parts[0]}.{parts[1]}".encode(), signature


def _base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


async def _get_jwks(
    settings: Settings, *, force_refresh: bool = False
) -> dict[str, dict[str, Any]]:
    now = time.monotonic()
    with _jwks_cache_lock:
        cached = _jwks_cache.get(settings.supabase_url)
        if not force_refresh and cached is not None and cached[0] > now:
            return cached[1]
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AuthenticationError("unable to load Supabase signing keys") from exc
    keys = payload.get("keys") if isinstance(payload, dict) else None
    if not isinstance(keys, list):
        raise AuthenticationError("Supabase signing keys are invalid")
    indexed = {
        key["kid"]: key for key in keys if isinstance(key, dict) and isinstance(key.get("kid"), str)
    }
    if not indexed:
        raise AuthenticationError("Supabase signing keys are empty")
    with _jwks_cache_lock:
        _jwks_cache[settings.supabase_url] = (now + settings.auth_jwks_cache_seconds, indexed)
    return indexed


def _verify_signature(
    algorithm: str, jwk: dict[str, Any], signed_data: bytes, signature: bytes
) -> None:
    try:
        if algorithm == "RS256" and jwk.get("kty") == "RSA":
            rsa_public_key = rsa.RSAPublicNumbers(
                int.from_bytes(_base64url_decode(str(jwk["e"])), "big"),
                int.from_bytes(_base64url_decode(str(jwk["n"])), "big"),
            ).public_key()
            rsa_public_key.verify(signature, signed_data, padding.PKCS1v15(), hashes.SHA256())
            return
        if algorithm == "ES256" and jwk.get("kty") == "EC" and jwk.get("crv") == "P-256":
            ec_public_key = ec.EllipticCurvePublicNumbers(
                int.from_bytes(_base64url_decode(str(jwk["x"])), "big"),
                int.from_bytes(_base64url_decode(str(jwk["y"])), "big"),
                ec.SECP256R1(),
            ).public_key()
            if len(signature) != 64:
                raise AuthenticationError("ES256 signature length is invalid")
            der_signature = utils.encode_dss_signature(
                int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
            )
            ec_public_key.verify(der_signature, signed_data, ec.ECDSA(hashes.SHA256()))
            return
    except (InvalidSignature, KeyError, ValueError) as exc:
        raise AuthenticationError("token signature is invalid") from exc
    raise AuthenticationError("token key type does not match its algorithm")


def _validate_claims(claims: dict[str, Any], settings: Settings) -> None:
    now = time.time()
    issuer = claims.get("iss")
    if issuer != settings.supabase_jwt_issuer:
        raise AuthenticationError("token issuer is invalid")
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if settings.supabase_jwt_audience not in audiences:
        raise AuthenticationError("token audience is invalid")
    expiration = claims.get("exp")
    if not isinstance(expiration, (int, float)) or expiration <= now:
        raise AuthenticationError("token is expired")
    not_before = claims.get("nbf")
    if isinstance(not_before, (int, float)) and not_before > now + 30:
        raise AuthenticationError("token is not active yet")
