from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from anyio import Path as AsyncPath

from mootcourt.core.config import get_settings
from mootcourt.db.session import dispose_engine, get_session_factory
from mootcourt.repositories.legal_search import ElasticsearchLegalSearchRepository
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.eval.legal_eval import load_legal_eval_dataset
from mootcourt.search.client import dispose_elasticsearch_client, get_elasticsearch_client
from mootcourt.search.embeddings import build_embedding_provider
from mootcourt.services.legal_eval import evaluate_legal_retrieval


async def _run(dataset_path: Path, output_path: Path | None) -> bool:
    settings = get_settings()
    dataset = load_legal_eval_dataset(dataset_path)
    embedding_provider = build_embedding_provider(settings, allow_candidate=True)
    index_name = f"{settings.elasticsearch_index_prefix}-legal-articles-{dataset.index_version}"
    repository = ElasticsearchLegalSearchRepository(
        get_elasticsearch_client(),
        index_name,
        embedding_dimensions=(
            embedding_provider.dimensions if embedding_provider is not None else None
        ),
        vector_similarity_threshold=settings.legal_vector_similarity_threshold,
        hybrid_candidate_multiplier=settings.legal_hybrid_candidate_multiplier,
        rrf_rank_constant=settings.legal_rrf_rank_constant,
    )
    try:
        async with get_session_factory()() as session:
            report = await evaluate_legal_retrieval(
                SqlAlchemyUnitOfWork(session),
                repository,
                dataset,
                index_name,
                embedding_provider,
            )
        rendered = report.model_dump_json(indent=2)
        if output_path is not None:
            async_output_path = AsyncPath(output_path)
            await async_output_path.parent.mkdir(parents=True, exist_ok=True)
            await async_output_path.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return report.passed
    finally:
        await dispose_elasticsearch_client()
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate configured legal retrieval against a reviewed JSON dataset"
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument(
        "--output", type=Path, help="optional path for the reproducible JSON report"
    )
    args = parser.parse_args()
    if not asyncio.run(_run(args.dataset, args.output)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
