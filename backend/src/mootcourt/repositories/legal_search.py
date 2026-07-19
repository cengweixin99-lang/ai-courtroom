from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Protocol

from elasticsearch import AsyncElasticsearch, NotFoundError

from mootcourt.schemas.legal_search import (
    LegalArticleDocument,
    LegalRetrievalMode,
    LegalSearchHit,
)

LEGAL_INDEX_MAPPINGS: dict[str, Any] = {
    "dynamic": "strict",
    "properties": {
        "source_id": {"type": "keyword"},
        "dataset_id": {"type": "keyword"},
        "index_version": {"type": "keyword"},
        "jurisdiction": {"type": "keyword"},
        "instrument_title": {
            "type": "text",
            "fields": {"keyword": {"type": "keyword"}},
        },
        "article_number": {
            "type": "text",
            "fields": {"keyword": {"type": "keyword"}},
        },
        "text": {"type": "text"},
        "effective_from": {"type": "date"},
        "effective_to": {"type": "date"},
        "official_source_url": {"type": "keyword", "index": False},
        "issuer": {"type": "keyword"},
        "source_type": {"type": "keyword"},
        "authority_level": {"type": "keyword"},
        "status": {"type": "keyword"},
        "review_status": {"type": "keyword"},
        "version_hash": {"type": "keyword"},
        "legal_conclusion_allowed": {"type": "boolean"},
        "embedding_version": {"type": "keyword"},
    },
}
_ARTICLE_REFERENCE = re.compile(
    r"第[零〇一二三四五六七八九十百千万两0-9]+条(?:第[零〇一二三四五六七八九十百千万两0-9]+款)?"
)


class LegalSearchRepositoryError(Exception):
    pass


class LegalSearchRepository(Protocol):
    async def search(
        self,
        *,
        query: str,
        jurisdiction: str,
        law_as_of_date: date,
        allowed_source_ids: Sequence[str],
        approved_review_statuses: Sequence[str],
        size: int,
        query_embedding: Sequence[float] | None = None,
        embedding_version: str | None = None,
    ) -> list[LegalSearchHit]: ...


class ElasticsearchLegalSearchRepository:
    def __init__(
        self,
        client: AsyncElasticsearch,
        index_name: str,
        *,
        embedding_dimensions: int | None = None,
        vector_similarity_threshold: float = 0.78,
        hybrid_candidate_multiplier: int = 4,
        rrf_rank_constant: int = 60,
    ) -> None:
        self._client = client
        self.index_name = index_name
        self._embedding_dimensions = embedding_dimensions
        self._vector_similarity_threshold = vector_similarity_threshold
        self._hybrid_candidate_multiplier = hybrid_candidate_multiplier
        self._rrf_rank_constant = rrf_rank_constant

    async def ensure_index(self) -> None:
        exists = await self._client.indices.exists(index=self.index_name)
        if not bool(exists):
            await self._client.indices.create(
                index=self.index_name,
                mappings=_index_mappings(self._embedding_dimensions),
                settings={"number_of_shards": 1, "number_of_replicas": 0},
            )
        elif self._embedding_dimensions is not None:
            await self._ensure_vector_mapping()

    async def _ensure_vector_mapping(self) -> None:
        response = await self._client.indices.get_mapping(index=self.index_name)
        index_mapping = response.get(self.index_name, {}).get("mappings", {})
        properties = index_mapping.get("properties", {})
        vector_mapping = properties.get("embedding") if isinstance(properties, dict) else None
        if vector_mapping is None:
            await self._client.indices.put_mapping(
                index=self.index_name,
                properties=_vector_properties(self._embedding_dimensions),
            )
            return
        if not isinstance(vector_mapping, dict) or vector_mapping.get("dims") != (
            self._embedding_dimensions
        ):
            raise LegalSearchRepositoryError(
                "existing legal index embedding dimensions do not match configuration"
            )

    async def index_documents(
        self,
        dataset_id: str,
        documents: Sequence[LegalArticleDocument],
    ) -> int:
        for document in documents:
            if document.embedding is not None and (
                self._embedding_dimensions is None
                or len(document.embedding) != self._embedding_dimensions
                or not document.embedding_version
            ):
                raise LegalSearchRepositoryError(
                    "embedded legal documents must match configured dimensions and version"
                )
        await self.ensure_index()
        operations: list[dict[str, Any]] = []
        for document in documents:
            # source_id 是稳定业务 ID；重复导入覆盖同一文档，不产生重复条款。
            operations.extend(
                [
                    {"index": {"_index": self.index_name, "_id": document.source_id}},
                    document.model_dump(mode="json", exclude_none=True),
                ]
            )
        if operations:
            response = await self._client.bulk(operations=operations, refresh="wait_for")
            if bool(response.get("errors")):
                raise LegalSearchRepositoryError("Elasticsearch bulk indexing failed")

        approved_ids = [item.source_id for item in documents]
        stale_query: dict[str, Any] = {"term": {"dataset_id": dataset_id}}
        if approved_ids:
            stale_query = {
                "bool": {
                    "filter": [{"term": {"dataset_id": dataset_id}}],
                    "must_not": [{"terms": {"source_id": approved_ids}}],
                }
            }
        await self._client.delete_by_query(
            index=self.index_name,
            query=stale_query,
            conflicts="proceed",
            refresh=True,
        )
        return len(documents)

    async def search(
        self,
        *,
        query: str,
        jurisdiction: str,
        law_as_of_date: date,
        allowed_source_ids: Sequence[str],
        approved_review_statuses: Sequence[str],
        size: int,
        query_embedding: Sequence[float] | None = None,
        embedding_version: str | None = None,
    ) -> list[LegalSearchHit]:
        if not allowed_source_ids:
            return []
        filters: list[dict[str, Any]] = [
            {"term": {"jurisdiction": jurisdiction}},
            {"term": {"status": "effective"}},
            {"terms": {"review_status": list(approved_review_statuses)}},
            {"terms": {"source_id": list(allowed_source_ids)}},
            {
                "bool": {
                    "should": [
                        {"range": {"effective_from": {"lte": law_as_of_date.isoformat()}}},
                        {"bool": {"must_not": {"exists": {"field": "effective_from"}}}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            {
                "bool": {
                    "should": [
                        {"range": {"effective_to": {"gte": law_as_of_date.isoformat()}}},
                        {"bool": {"must_not": {"exists": {"field": "effective_to"}}}},
                    ],
                    "minimum_should_match": 1,
                }
            },
        ]
        try:
            if query_embedding is None:
                response = await self._client.search(
                    index=self.index_name,
                    size=size,
                    query=_build_search_query(query, filters),
                    source_excludes=["embedding"],
                )
                raw_hits = _raw_hits(response)
                return [
                    _search_hit(item).model_copy(
                        update={"bm25_score": _raw_score(item), "bm25_rank": rank}
                    )
                    for rank, item in enumerate(raw_hits, start=1)
                ]
            if self._embedding_dimensions is None or len(query_embedding) != (
                self._embedding_dimensions
            ):
                raise LegalSearchRepositoryError("query embedding dimensions do not match index")
            if not embedding_version:
                raise LegalSearchRepositoryError("query embedding version is required")
            candidate_size = min(size * self._hybrid_candidate_multiplier, 100)
            vector_filters = [*filters, {"term": {"embedding_version": embedding_version}}]
            bm25_response, vector_response = await asyncio.gather(
                self._client.search(
                    index=self.index_name,
                    size=candidate_size,
                    query=_build_search_query(query, filters),
                    source_excludes=["embedding"],
                ),
                self._client.search(
                    index=self.index_name,
                    size=candidate_size,
                    knn={
                        "field": "embedding",
                        "query_vector": list(query_embedding),
                        "k": candidate_size,
                        "num_candidates": min(max(candidate_size * 4, 100), 10_000),
                        "filter": vector_filters,
                        "similarity": self._vector_similarity_threshold,
                    },
                    source_excludes=["embedding"],
                ),
            )
            return _rrf_fuse(
                _raw_hits(bm25_response),
                _raw_hits(vector_response),
                size=size,
                rank_constant=self._rrf_rank_constant,
            )
        except NotFoundError as exc:
            raise LegalSearchRepositoryError("legal article index does not exist") from exc


def _index_mappings(embedding_dimensions: int | None) -> dict[str, Any]:
    if embedding_dimensions is None:
        return LEGAL_INDEX_MAPPINGS
    properties = dict(LEGAL_INDEX_MAPPINGS["properties"])
    properties.update(_vector_properties(embedding_dimensions))
    return {"dynamic": "strict", "properties": properties}


def _vector_properties(embedding_dimensions: int | None) -> dict[str, Any]:
    if embedding_dimensions is None:
        raise LegalSearchRepositoryError("embedding dimensions are required")
    return {
        "embedding": {
            "type": "dense_vector",
            "dims": embedding_dimensions,
            "index": True,
            "similarity": "cosine",
        },
        "embedding_version": {"type": "keyword"},
    }


def _raw_hits(response: object) -> list[dict[str, Any]]:
    body = getattr(response, "body", response)
    if not isinstance(body, Mapping):
        raise LegalSearchRepositoryError("Elasticsearch returned an invalid response")
    raw_hits = body.get("hits", {}).get("hits", [])
    if not isinstance(raw_hits, list) or not all(isinstance(item, dict) for item in raw_hits):
        raise LegalSearchRepositoryError("Elasticsearch returned invalid hits")
    return raw_hits


def _raw_score(raw_hit: dict[str, Any]) -> float:
    return float(raw_hit.get("_score") or 0)


def _rrf_fuse(
    bm25_raw_hits: list[dict[str, Any]],
    vector_raw_hits: list[dict[str, Any]],
    *,
    size: int,
    rank_constant: int,
) -> list[LegalSearchHit]:
    hits_by_id: dict[str, LegalSearchHit] = {}
    scores: dict[str, float] = {}
    updates: dict[str, dict[str, float | int | LegalRetrievalMode]] = {}
    for mode, raw_hits in (("bm25", bm25_raw_hits), ("vector", vector_raw_hits)):
        for rank, raw_hit in enumerate(raw_hits, start=1):
            hit = _search_hit(raw_hit)
            hits_by_id.setdefault(hit.source_id, hit)
            scores[hit.source_id] = scores.get(hit.source_id, 0) + 1 / (rank_constant + rank)
            update = updates.setdefault(
                hit.source_id, {"retrieval_mode": LegalRetrievalMode.HYBRID_RRF}
            )
            update[f"{mode}_score"] = _raw_score(raw_hit)
            update[f"{mode}_rank"] = rank
    ordered_ids = sorted(scores, key=lambda source_id: (-scores[source_id], source_id))[:size]
    return [
        hits_by_id[source_id].model_copy(update={**updates[source_id], "score": scores[source_id]})
        for source_id in ordered_ids
    ]


def _search_hit(raw_hit: object) -> LegalSearchHit:
    if not isinstance(raw_hit, dict) or not isinstance(raw_hit.get("_source"), dict):
        raise LegalSearchRepositoryError("Elasticsearch hit is missing _source")
    source = raw_hit["_source"]
    return LegalSearchHit(
        source_id=str(source["source_id"]),
        instrument_title=str(source["instrument_title"]),
        article_number=str(source["article_number"]),
        text=str(source["text"]),
        jurisdiction=str(source["jurisdiction"]),
        effective_from=source.get("effective_from"),
        effective_to=source.get("effective_to"),
        status=str(source["status"]),
        review_status=str(source["review_status"]),
        authority_level=str(source["authority_level"]),
        official_source_url=source.get("official_source_url"),
        version_hash=source.get("version_hash"),
        score=_raw_score(raw_hit),
    )


def _build_search_query(query: str, filters: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = [
        {
            "multi_match": {
                "query": query,
                "fields": ["text^3", "article_number^2", "instrument_title"],
                "type": "best_fields",
                # 小型法律库不能把任意公共汉字命中都视为法律依据；至少匹配多数查询词项。
                "minimum_should_match": "60%",
            }
        }
    ]
    article_references = sorted(set(_ARTICLE_REFERENCE.findall(query)))
    if article_references:
        candidates.append(
            {
                "terms": {
                    "article_number.keyword": article_references,
                    "boost": 10,
                }
            }
        )
    return {
        "bool": {
            "should": candidates,
            "minimum_should_match": 1,
            "filter": filters,
        }
    }
