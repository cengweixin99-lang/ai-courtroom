from __future__ import annotations

import secrets

from mootcourt.core.config import Settings

DIAGNOSTICS_KEY_HEADER = "X-Diagnostics-Key"


def diagnostics_access_allowed(provided_key: str | None, settings: Settings) -> bool:
    """开发环境未配置密钥时保持兼容；生产环境和显式配置后必须通过认证。"""

    expected = settings.diagnostics_api_key.get_secret_value()
    authentication_required = settings.app_env.lower() == "production" or bool(expected)
    if not authentication_required:
        return True
    if provided_key is None or not expected:
        return False
    # 常量时间比较避免密钥前缀通过响应时间被逐字探测。
    return secrets.compare_digest(
        provided_key.encode("utf-8"),
        expected.encode("utf-8"),
    )
