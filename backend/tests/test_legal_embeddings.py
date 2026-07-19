from __future__ import annotations

from pathlib import Path

from mootcourt.schemas.legal_search import load_legal_source_manifest
from mootcourt.services.legal_embeddings import embed_legal_documents

LEGAL_MANIFEST = Path(__file__).parents[2] / "knowledge" / "legal" / "source_manifest.json"


class RecordingEmbeddingProvider:
    model_name = "test-model"
    version = "test-model-v1"
    dimensions = 3

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]


async def test_embed_legal_documents_batches_complete_articles() -> None:
    _, documents = load_legal_source_manifest(LEGAL_MANIFEST)
    provider = RecordingEmbeddingProvider()

    embedded = await embed_legal_documents(documents[:3], provider, batch_size=2)

    assert [len(batch) for batch in provider.batches] == [2, 1]
    assert documents[0].instrument_title in provider.batches[0][0]
    assert documents[0].article_number in provider.batches[0][0]
    assert documents[0].text in provider.batches[0][0]
    assert embedded[0].embedding_version == "test-model-v1"
    assert embedded[0].embedding == [1.0, 0.0, 0.0]
