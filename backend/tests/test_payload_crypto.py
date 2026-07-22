from datetime import datetime

import pytest

from mootcourt.core.payload_crypto import (
    PayloadCryptoError,
    decrypt_payload,
    encrypt_payload,
)
from mootcourt.services.agent_data_maintenance import (
    purge_agent_data,
    redact_existing_agent_traces,
)


def test_encrypted_payload_round_trip_and_does_not_store_plaintext() -> None:
    payload = {"speech": "仅用于回放的敏感内容", "tokens": 12}
    envelope = encrypt_payload(payload, "k" * 32)
    assert envelope["encrypted"] is True
    assert "仅用于回放的敏感内容" not in str(envelope)
    assert decrypt_payload(envelope, "k" * 32) == payload


def test_historical_plaintext_payload_remains_compatible() -> None:
    payload = {"status": "completed", "value": 1}
    assert encrypt_payload(payload, "") is payload
    assert decrypt_payload(payload, "") == payload


def test_invalid_ciphertext_is_not_exposed() -> None:
    with pytest.raises(PayloadCryptoError):
        decrypt_payload({"encrypted": True, "version": "v1", "ciphertext": "invalid"}, "k" * 32)


@pytest.mark.asyncio
async def test_agent_data_maintenance_reports_empty_batches() -> None:
    class Traces:
        async def list_legacy(self, *, limit: int) -> list[object]:
            return []

        async def delete_older_than(self, cutoff: datetime) -> int:
            return 2

    class Invocations:
        async def delete_finished_older_than(self, cutoff: datetime) -> int:
            return 1

    commits = 0

    async def commit() -> None:
        nonlocal commits
        commits += 1

    class UnitOfWork:
        agent_traces = Traces()
        agent_invocations = Invocations()

        async def commit(self) -> None:
            await commit()

    unit_of_work = UnitOfWork()
    redaction = await redact_existing_agent_traces(unit_of_work, hmac_key="h" * 32)
    purge = await purge_agent_data(unit_of_work, older_than_days=30, invocation_older_than_days=7)
    assert redaction.processed == 0 and redaction.failed == 0
    assert purge.traces_deleted == 2 and purge.invocations_deleted == 1
    assert commits == 1
