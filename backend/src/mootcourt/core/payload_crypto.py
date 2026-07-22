from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class PayloadCryptoError(ValueError):
    """Raised when an encrypted idempotency payload cannot be decoded."""


def encrypt_payload(payload: dict[str, Any], key: str) -> dict[str, Any]:
    if not key:
        return payload
    token = _fernet(key).encrypt(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    return {"encrypted": True, "version": "v1", "ciphertext": token.decode("ascii")}


def decrypt_payload(value: Any, key: str) -> dict[str, Any]:
    if not _is_envelope(value):
        if isinstance(value, dict):
            return value
        raise PayloadCryptoError("idempotency payload is not an object")
    if not key:
        raise PayloadCryptoError("idempotency payload key is not configured")
    try:
        raw = _fernet(key).decrypt(value["ciphertext"].encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (
        InvalidToken,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        AttributeError,
        ValueError,
    ) as exc:
        raise PayloadCryptoError("idempotency payload decryption failed") from exc
    if not isinstance(payload, dict):
        raise PayloadCryptoError("idempotency payload is not an object")
    return payload


def _is_envelope(value: Any) -> bool:
    return isinstance(value, dict) and value.get("encrypted") is True


def _fernet(key: str) -> Fernet:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))
