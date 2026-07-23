from __future__ import annotations

import logging

import pytest

from mootcourt.core import logging as application_logging
from mootcourt.core.config import Settings


def test_configure_logging_applies_configured_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        application_logging,
        "get_settings",
        lambda: Settings(log_level="DEBUG"),
    )

    application_logging.configure_logging()

    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger().handlers
