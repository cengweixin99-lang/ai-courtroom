from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from elasticsearch import AsyncElasticsearch

from mootcourt.core.config import get_settings
from mootcourt.repositories.legal_search import ElasticsearchLegalSearchRepository
from mootcourt.schemas.legal_search import LegalIndexResult, load_legal_source_manifest
from mootcourt.search.embeddings import build_embedding_provider
from mootcourt.services.legal_embeddings import embed_legal_documents


async def _run(manifest_path: Path) -> None:
    settings = get_settings()
    manifest, documents = load_legal_source_manifest(manifest_path)
    embedding_provider = build_embedding_provider(settings, allow_candidate=True)
    if embedding_provider is not None:
        documents = await embed_legal_documents(
            documents, embedding_provider, settings.legal_embedding_batch_size
        )
    index_name = f"{settings.elasticsearch_index_prefix}-legal-articles-{manifest.index_version}"
    client = AsyncElasticsearch(
        settings.elasticsearch_url,
        request_timeout=30,
        retry_on_timeout=True,
        max_retries=2,
    )
    try:
        repository = ElasticsearchLegalSearchRepository(
            client,
            index_name,
            embedding_dimensions=(
                embedding_provider.dimensions if embedding_provider is not None else None
            ),
        )
        indexed_count = await repository.index_documents(manifest.dataset_id, documents)
    finally:
        await client.close()
    print(
        LegalIndexResult(
            dataset_id=manifest.dataset_id,
            index_name=index_name,
            indexed_count=indexed_count,
            release_blockers=manifest.release_blockers,
            embedding_version=(
                embedding_provider.version if embedding_provider is not None else None
            ),
        ).model_dump_json()
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and index approved legal article snapshots into Elasticsearch"
    )
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    asyncio.run(_run(args.manifest))


if __name__ == "__main__":
    main()
