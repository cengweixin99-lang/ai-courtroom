from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mootcourt.schemas.eval.embedding_models import load_embedding_model_registry

MODEL_REGISTRY = Path(__file__).parents[2] / "knowledge" / "legal" / "embedding_models.json"


def test_embedding_model_registry_loads_candidate_profile() -> None:
    registry = load_embedding_model_registry(MODEL_REGISTRY)

    assert len(registry.models) == 1
    assert registry.models[0].model == "bge-m3"
    assert registry.models[0].dimensions == 1024
    assert registry.models[0].review_status == "automated_eval_passed_pending_human_review"
    assert registry.models[0].enabled_for_runtime is False


def test_embedding_model_registry_rejects_duplicate_versions(tmp_path: Path) -> None:
    raw = json.loads(MODEL_REGISTRY.read_text(encoding="utf-8"))
    duplicate = dict(raw["models"][0])
    duplicate["id"] = "another-id"
    raw["models"].append(duplicate)
    path = tmp_path / "duplicate-models.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValidationError, match="embedding versions must be unique"):
        load_embedding_model_registry(path)
