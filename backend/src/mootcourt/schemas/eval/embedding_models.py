from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field, model_validator

from mootcourt.core.config import Settings
from mootcourt.schemas.legal_search import StrictLegalModel


class LegalEmbeddingModelProfile(StrictLegalModel):
    id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    embedding_version: str = Field(min_length=1)
    dimensions: int = Field(ge=8, le=4096)
    language_scope: list[str] = Field(min_length=1)
    intended_use: str = Field(min_length=1)
    review_status: str = Field(min_length=1)
    enabled_for_runtime: bool
    notes: str = Field(min_length=1)


class LegalEmbeddingModelRegistry(StrictLegalModel):
    schema_version: str = Field(min_length=1)
    models: list[LegalEmbeddingModelProfile] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_models(self) -> LegalEmbeddingModelRegistry:
        ids = [item.id for item in self.models]
        versions = [item.embedding_version for item in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("embedding model profile IDs must be unique")
        if len(versions) != len(set(versions)):
            raise ValueError("embedding versions must be unique")
        return self

    def require_configured_model(self, settings: Settings) -> LegalEmbeddingModelProfile:
        matches = [
            item
            for item in self.models
            if item.provider == settings.legal_embedding_provider
            and item.model == settings.legal_embedding_model
            and item.embedding_version == settings.legal_embedding_version
            and item.dimensions == settings.legal_embedding_dimensions
        ]
        if len(matches) != 1:
            raise ValueError(
                "configured legal embedding must exactly match one reviewed registry profile"
            )
        return matches[0]


def load_embedding_model_registry(registry_path: Path) -> LegalEmbeddingModelRegistry:
    try:
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing embedding model registry: {registry_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {registry_path}: {exc}") from exc
    return LegalEmbeddingModelRegistry.model_validate(raw)
