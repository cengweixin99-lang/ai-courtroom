from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mootcourt.api.dependencies import (
    get_legal_search_repository,
    get_unit_of_work,
    require_authenticated_principal,
)
from mootcourt.core.auth import AuthenticatedPrincipal
from mootcourt.main import app
from mootcourt.repositories.legal_search import (
    LEGAL_INDEX_MAPPINGS,
    ElasticsearchLegalSearchRepository,
    _build_search_query,
    _rrf_fuse,
)
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.legal_search import (
    LegalSearchHit,
    LegalSearchRequest,
    load_legal_source_manifest,
)
from mootcourt.services.case_importer import import_case_package
from mootcourt.services.legal_search import search_case_law

LEGAL_MANIFEST = Path(__file__).parents[2] / "knowledge" / "legal" / "source_manifest.json"
CASE_PACKAGE = Path(__file__).parents[2] / "data" / "authoring" / "CASE-001"


class StubLegalSearchRepository:
    def __init__(self, hits: list[LegalSearchHit] | None = None) -> None:
        self.hits = hits or []
        self.calls: list[dict[str, Any]] = []

    async def search(self, **kwargs: Any) -> list[LegalSearchHit]:
        self.calls.append(kwargs)
        return self.hits


class RecordingIndices:
    def __init__(self, exists: bool) -> None:
        self._exists = exists
        self.created: list[dict[str, Any]] = []
        self.put_mappings: list[dict[str, Any]] = []

    async def exists(self, **kwargs: Any) -> bool:
        return self._exists

    async def create(self, **kwargs: Any) -> dict[str, bool]:
        self.created.append(kwargs)
        self._exists = True
        return {"acknowledged": True}

    async def get_mapping(self, **kwargs: Any) -> dict[str, Any]:
        return {kwargs["index"]: {"mappings": {"properties": {}}}}

    async def put_mapping(self, **kwargs: Any) -> dict[str, bool]:
        self.put_mappings.append(kwargs)
        return {"acknowledged": True}


class RecordingElasticsearchClient:
    def __init__(self, *, index_exists: bool = False) -> None:
        self.indices = RecordingIndices(index_exists)
        self.bulk_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []
        self.search_response: dict[str, Any] = {"hits": {"hits": []}}

    async def bulk(self, **kwargs: Any) -> dict[str, bool]:
        self.bulk_calls.append(kwargs)
        return {"errors": False}

    async def delete_by_query(self, **kwargs: Any) -> dict[str, int]:
        self.delete_calls.append(kwargs)
        return {"deleted": 0}

    async def search(self, **kwargs: Any) -> dict[str, Any]:
        self.search_calls.append(kwargs)
        return self.search_response


@pytest_asyncio.fixture
async def legal_api_client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        await import_case_package(unit_of_work, CASE_PACKAGE)
        await unit_of_work.commit()

    async def override_unit_of_work() -> AsyncIterator[SqlAlchemyUnitOfWork]:
        async with session_factory() as session:
            unit_of_work = SqlAlchemyUnitOfWork(session)
            try:
                yield unit_of_work
                await unit_of_work.commit()
            except Exception:
                await unit_of_work.rollback()
                raise

    app.dependency_overrides[get_unit_of_work] = override_unit_of_work
    app.dependency_overrides[require_authenticated_principal] = lambda: AuthenticatedPrincipal(
        subject="test-legal-user",
        email="legal@example.test",
        provider_role="authenticated",
        claims={"sub": "test-legal-user"},
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def test_manifest_loads_only_explicitly_approved_sources() -> None:
    manifest, documents = load_legal_source_manifest(LEGAL_MANIFEST)

    assert len(documents) == 10
    assert {item.source_id for item in documents} == set(manifest.approved_source_ids)
    assert all(item.legal_conclusion_allowed is False for item in documents)
    assert "LS-CRIMINAL-LAW-264-2009-HISTORICAL" in manifest.approved_source_ids
    assert manifest.release_blockers


def test_manifest_rejects_unapproved_review_status(tmp_path: Path) -> None:
    original = json.loads(LEGAL_MANIFEST.read_text(encoding="utf-8"))
    original["approved_review_statuses"] = ["verified"]
    original["source_data_path"] = str(
        (LEGAL_MANIFEST.parent / original["source_data_path"]).resolve()
    )
    manifest_path = tmp_path / "source_manifest.json"
    manifest_path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="unapproved review statuses"):
        load_legal_source_manifest(manifest_path)


def test_bm25_query_requires_majority_match_and_boosts_article_number() -> None:
    query = _build_search_query("盗窃罪第二百六十四条的入罪条件", [])

    assert query["bool"]["minimum_should_match"] == 1
    candidates = query["bool"]["should"]
    assert candidates[0]["multi_match"]["minimum_should_match"] == "60%"
    assert candidates[1]["terms"]["article_number.keyword"] == ["第二百六十四条"]
    assert candidates[1]["terms"]["boost"] == 10


def test_bm25_query_expands_synonyms_without_weakening_primary_clause() -> None:
    query = _build_search_query(
        "林舟是否明知相机为他人财物",
        [],
        {"故意": ("明知", "蓄意"), "盗窃": ("偷窃", "窃取")},
    )

    candidates = query["bool"]["should"]
    # 主查询仍是原始文本与 60% 词项门槛
    assert candidates[0]["multi_match"]["query"] == "林舟是否明知相机为他人财物"
    assert candidates[0]["multi_match"]["minimum_should_match"] == "60%"
    # 命中“明知”触发“故意”组扩展；未命中“盗窃”组则不扩展
    assert len(candidates) == 2
    synonym_clause = candidates[1]["multi_match"]
    assert synonym_clause["query"] == "故意 明知 蓄意"
    assert synonym_clause["fields"] == ["text"]
    assert synonym_clause["boost"] == 0.5


def test_bm25_query_skips_synonym_expansion_for_unrelated_text() -> None:
    query = _build_search_query(
        "法庭调查程序",
        [],
        {"故意": ("明知",), "盗窃": ("偷窃",)},
    )

    assert len(query["bool"]["should"]) == 1


async def test_repository_creates_index_and_idempotently_indexes_documents() -> None:
    manifest, documents = load_legal_source_manifest(LEGAL_MANIFEST)
    client = RecordingElasticsearchClient()
    repository = ElasticsearchLegalSearchRepository(
        cast(Any, client), "mootcourt-legal-articles-v1"
    )

    indexed = await repository.index_documents(manifest.dataset_id, documents[:2])

    assert indexed == 2
    assert client.indices.created[0]["mappings"] == LEGAL_INDEX_MAPPINGS
    operations = client.bulk_calls[0]["operations"]
    assert operations[0]["index"]["_id"] == documents[0].source_id
    assert operations[2]["index"]["_id"] == documents[1].source_id
    assert operations[1]["legal_conclusion_allowed"] is False
    stale_query = client.delete_calls[0]["query"]
    assert stale_query["bool"]["filter"] == [{"term": {"dataset_id": manifest.dataset_id}}]
    assert stale_query["bool"]["must_not"][0]["terms"]["source_id"] == [
        documents[0].source_id,
        documents[1].source_id,
    ]


async def test_repository_creates_dense_vector_mapping_and_indexes_version() -> None:
    manifest, documents = load_legal_source_manifest(LEGAL_MANIFEST)
    document = documents[0].model_copy(
        update={"embedding_version": "legal-test-v1", "embedding": [1.0, 0.0, 0.0]}
    )
    client = RecordingElasticsearchClient()
    repository = ElasticsearchLegalSearchRepository(
        cast(Any, client), "mootcourt-legal-articles-v1", embedding_dimensions=3
    )

    await repository.index_documents(manifest.dataset_id, [document])

    properties = client.indices.created[0]["mappings"]["properties"]
    assert properties["embedding"] == {
        "type": "dense_vector",
        "dims": 3,
        "index": True,
        "similarity": "cosine",
    }
    indexed_source = client.bulk_calls[0]["operations"][1]
    assert indexed_source["embedding_version"] == "legal-test-v1"
    assert indexed_source["embedding"] == [1.0, 0.0, 0.0]


async def test_repository_adds_vector_mapping_to_existing_bm25_index() -> None:
    manifest, documents = load_legal_source_manifest(LEGAL_MANIFEST)
    document = documents[0].model_copy(
        update={"embedding_version": "legal-test-v1", "embedding": [1.0, 0.0, 0.0]}
    )
    client = RecordingElasticsearchClient(index_exists=True)
    repository = ElasticsearchLegalSearchRepository(
        cast(Any, client), "mootcourt-legal-articles-v1", embedding_dimensions=3
    )

    await repository.index_documents(manifest.dataset_id, [document])

    assert client.indices.created == []
    assert client.indices.put_mappings[0]["properties"]["embedding"]["dims"] == 3


async def test_repository_search_applies_all_mandatory_filters() -> None:
    client = RecordingElasticsearchClient(index_exists=True)
    client.search_response = {
        "hits": {
            "hits": [
                {
                    "_score": 12.5,
                    "_source": {
                        "source_id": "LS-CRIMINAL-LAW-264",
                        "instrument_title": "中华人民共和国刑法",
                        "article_number": "第二百六十四条",
                        "text": "盗窃公私财物……",
                        "jurisdiction": "PRC",
                        "effective_from": "2021-03-01",
                        "effective_to": None,
                        "status": "effective",
                        "review_status": "verified",
                        "authority_level": "law_current_official",
                        "official_source_url": "https://flk.npc.gov.cn/",
                        "version_hash": "a" * 64,
                    },
                }
            ]
        }
    }
    repository = ElasticsearchLegalSearchRepository(
        cast(Any, client), "mootcourt-legal-articles-v1"
    )

    hits = await repository.search(
        query="盗窃罪第二百六十四条的入罪条件",
        jurisdiction="PRC",
        law_as_of_date=date(2026, 7, 14),
        allowed_source_ids=["LS-CRIMINAL-LAW-264"],
        approved_review_statuses=["verified"],
        size=5,
    )

    assert [item.source_id for item in hits] == ["LS-CRIMINAL-LAW-264"]
    assert hits[0].score == 12.5
    search_call = client.search_calls[0]
    filters = search_call["query"]["bool"]["filter"]
    assert {"term": {"jurisdiction": "PRC"}} in filters
    assert {"term": {"status": "effective"}} in filters
    assert {"terms": {"review_status": ["verified"]}} in filters
    assert {"terms": {"source_id": ["LS-CRIMINAL-LAW-264"]}} in filters
    serialized = json.dumps(filters)
    assert "2026-07-14" in serialized
    assert "superseded_not_applicable" not in serialized


async def test_hybrid_search_applies_filters_and_embedding_version() -> None:
    client = RecordingElasticsearchClient(index_exists=True)
    client.search_response = {"hits": {"hits": []}}
    repository = ElasticsearchLegalSearchRepository(
        cast(Any, client),
        "mootcourt-legal-articles-v1",
        embedding_dimensions=3,
        vector_similarity_threshold=0.8,
    )

    hits = await repository.search(
        query="未经授权取走财物如何定性",
        jurisdiction="PRC",
        law_as_of_date=date(2026, 7, 14),
        allowed_source_ids=["LS-CRIMINAL-LAW-264"],
        approved_review_statuses=["verified"],
        size=5,
        query_embedding=[1.0, 0.0, 0.0],
        embedding_version="legal-test-v1",
    )

    assert hits == []
    assert len(client.search_calls) == 2
    vector_call = next(call for call in client.search_calls if "knn" in call)
    vector_filters = vector_call["knn"]["filter"]
    assert {"term": {"jurisdiction": "PRC"}} in vector_filters
    assert {"term": {"status": "effective"}} in vector_filters
    assert {"terms": {"source_id": ["LS-CRIMINAL-LAW-264"]}} in vector_filters
    assert {"term": {"embedding_version": "legal-test-v1"}} in vector_filters
    assert vector_call["knn"]["similarity"] == 0.8


def test_rrf_fusion_preserves_both_scores_and_ranks() -> None:
    def raw_hit(source_id: str, score: float) -> dict[str, Any]:
        return {
            "_score": score,
            "_source": {
                "source_id": source_id,
                "instrument_title": "中华人民共和国刑法",
                "article_number": "第二百六十四条",
                "text": "盗窃公私财物……",
                "jurisdiction": "PRC",
                "effective_from": "2021-03-01",
                "effective_to": None,
                "status": "effective",
                "review_status": "verified",
                "authority_level": "law_current_official",
                "official_source_url": "https://flk.npc.gov.cn/",
                "version_hash": "a" * 64,
            },
        }

    fused = _rrf_fuse(
        [raw_hit("LAW-A", 9), raw_hit("LAW-B", 8)],
        [raw_hit("LAW-B", 0.95), raw_hit("LAW-C", 0.9)],
        size=3,
        rank_constant=60,
    )

    assert [hit.source_id for hit in fused] == ["LAW-B", "LAW-A", "LAW-C"]
    assert fused[0].retrieval_mode == "hybrid_rrf"
    assert fused[0].bm25_score == 8
    assert fused[0].vector_score == 0.95
    assert fused[0].bm25_rank == 2
    assert fused[0].vector_rank == 1


async def test_repository_skips_search_when_profile_has_no_sources() -> None:
    client = RecordingElasticsearchClient(index_exists=True)
    repository = ElasticsearchLegalSearchRepository(
        cast(Any, client), "mootcourt-legal-articles-v1"
    )

    hits = await repository.search(
        query="盗窃罪",
        jurisdiction="PRC",
        law_as_of_date=date(2026, 7, 14),
        allowed_source_ids=[],
        approved_review_statuses=["verified"],
        size=5,
    )

    assert hits == []
    assert client.search_calls == []


async def test_service_maps_empty_hits_to_insufficient_authority(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        await import_case_package(unit_of_work, CASE_PACKAGE)
        await unit_of_work.commit()
        repository = StubLegalSearchRepository()

        result = await search_case_law(
            unit_of_work,
            repository,
            LegalSearchRequest(case_id="CASE-001", query="不存在的法律问题"),
        )

    assert result is not None
    assert result.outcome == "INSUFFICIENT_LEGAL_AUTHORITY"
    assert result.hits == []
    assert repository.calls[0]["jurisdiction"] == "PRC"


async def test_legal_search_uses_case_profile_filters(
    legal_api_client: AsyncClient,
) -> None:
    repository = StubLegalSearchRepository(
        [
            LegalSearchHit(
                source_id="LS-CRIMINAL-LAW-264",
                instrument_title="中华人民共和国刑法",
                article_number="第二百六十四条",
                text="盗窃公私财物……",
                jurisdiction="PRC",
                effective_from=date(2021, 3, 1),
                effective_to=None,
                status="effective",
                review_status="verified",
                authority_level="law_current_official",
                official_source_url="https://flk.npc.gov.cn/",
                version_hash="a" * 64,
                score=8.5,
            )
        ]
    )
    app.dependency_overrides[get_legal_search_repository] = lambda: repository

    response = await legal_api_client.post(
        "/api/v1/legal/search",
        json={
            "case_id": "CASE-001",
            "query": "盗窃罪第二百六十四条的入罪条件",
            "top_k": 3,
        },
    )

    assert response.status_code == 200
    assert response.json()["outcome"] == "SUFFICIENT_LEGAL_AUTHORITY"
    assert response.json()["trace_id"]
    call = repository.calls[0]
    assert call["jurisdiction"] == "PRC"
    assert str(call["law_as_of_date"]) == "2026-07-14"
    assert "LS-CRIMINAL-LAW-264" in call["allowed_source_ids"]
    assert "LS-CRIMINAL-LAW-264-2009-HISTORICAL" not in call["allowed_source_ids"]
    assert call["approved_review_statuses"] == (
        "verified",
        "official_publication_verified_historical_version",
    )
    assert call["size"] == 3


async def test_legal_search_min_score_marks_weak_hits_insufficient(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    weak_hit = LegalSearchHit(
        source_id="LS-CRIMINAL-LAW-264",
        instrument_title="中华人民共和国刑法",
        article_number="第二百六十四条",
        text="盗窃公私财物……",
        jurisdiction="PRC",
        effective_from=date(2021, 3, 1),
        effective_to=None,
        status="effective",
        review_status="verified",
        authority_level="law_current_official",
        official_source_url="https://flk.npc.gov.cn/",
        version_hash="a" * 64,
        score=1.5,
    )
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        await import_case_package(unit_of_work, CASE_PACKAGE)
        await unit_of_work.commit()
        repository = StubLegalSearchRepository([weak_hit])

        default_result = await search_case_law(
            unit_of_work,
            repository,
            LegalSearchRequest(case_id="CASE-001", query="盗窃", top_k=3),
        )
        threshold_result = await search_case_law(
            unit_of_work,
            repository,
            LegalSearchRequest(case_id="CASE-001", query="盗窃", top_k=3),
            min_score=5.0,
        )

    assert default_result is not None
    assert default_result.outcome == "SUFFICIENT_LEGAL_AUTHORITY"
    assert threshold_result is not None
    assert threshold_result.outcome == "INSUFFICIENT_LEGAL_AUTHORITY"
    assert threshold_result.hits == []


async def test_legal_search_returns_insufficient_without_model_fallback(
    legal_api_client: AsyncClient,
) -> None:
    repository = StubLegalSearchRepository()
    app.dependency_overrides[get_legal_search_repository] = lambda: repository

    response = await legal_api_client.post(
        "/api/v1/legal/search",
        json={"case_id": "CASE-001", "query": "不存在的法律问题"},
    )

    assert response.status_code == 200
    assert response.json()["outcome"] == "INSUFFICIENT_LEGAL_AUTHORITY"
    assert response.json()["hits"] == []


async def test_legal_search_unknown_case_returns_404(legal_api_client: AsyncClient) -> None:
    repository = StubLegalSearchRepository()
    app.dependency_overrides[get_legal_search_repository] = lambda: repository

    response = await legal_api_client.post(
        "/api/v1/legal/search",
        json={"case_id": "CASE-404", "query": "盗窃罪"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "case_not_found"
    assert repository.calls == []


async def test_legal_search_trace_and_valid_citation_round_trip(
    legal_api_client: AsyncClient,
) -> None:
    hit = LegalSearchHit(
        source_id="LS-CRIMINAL-LAW-264",
        instrument_title="中华人民共和国刑法",
        article_number="第二百六十四条",
        text="盗窃公私财物……",
        jurisdiction="PRC",
        effective_from=date(2021, 3, 1),
        effective_to=None,
        status="effective",
        review_status="verified",
        authority_level="law_current_official",
        official_source_url="https://flk.npc.gov.cn/detail?id=current",
        version_hash="a" * 64,
        score=8.5,
        bm25_score=8.5,
        bm25_rank=1,
    )
    app.dependency_overrides[get_legal_search_repository] = lambda: StubLegalSearchRepository([hit])
    search_response = await legal_api_client.post(
        "/api/v1/legal/search",
        json={"case_id": "CASE-001", "query": "盗窃罪第二百六十四条"},
    )
    trace_id = search_response.json()["trace_id"]

    trace_response = await legal_api_client.get(f"/api/v1/legal/search-traces/{trace_id}")
    citation_response = await legal_api_client.post(
        "/api/v1/legal/citations/validate",
        json={
            "trace_id": trace_id,
            "citations": [
                {
                    "source_id": hit.source_id,
                    "article_number": hit.article_number,
                    "text": hit.text,
                    "official_source_url": hit.official_source_url,
                    "version_hash": hit.version_hash,
                }
            ],
        },
    )

    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert trace["case_id"] == "CASE-001"
    assert trace["filters"]["jurisdiction"] == "PRC"
    assert "LS-CRIMINAL-LAW-264-2009-HISTORICAL" not in trace["filters"]["allowed_source_ids"]
    assert trace["hits"][0]["source_id"] == hit.source_id
    assert "embedding" not in trace["hits"][0]
    assert citation_response.status_code == 200
    assert citation_response.json()["valid"] is True
    assert citation_response.json()["results"][0]["failures"] == []


async def test_citation_validation_rejects_fabrication_and_tampering(
    legal_api_client: AsyncClient,
) -> None:
    hit = LegalSearchHit(
        source_id="LS-CRIMINAL-LAW-264",
        instrument_title="中华人民共和国刑法",
        article_number="第二百六十四条",
        text="盗窃公私财物……",
        jurisdiction="PRC",
        effective_from=date(2021, 3, 1),
        effective_to=None,
        status="effective",
        review_status="verified",
        authority_level="law_current_official",
        official_source_url="https://flk.npc.gov.cn/detail?id=current",
        version_hash="a" * 64,
        score=8.5,
    )
    app.dependency_overrides[get_legal_search_repository] = lambda: StubLegalSearchRepository([hit])
    search_response = await legal_api_client.post(
        "/api/v1/legal/search",
        json={"case_id": "CASE-001", "query": "盗窃罪第二百六十四条"},
    )
    trace_id = search_response.json()["trace_id"]

    response = await legal_api_client.post(
        "/api/v1/legal/citations/validate",
        json={
            "trace_id": trace_id,
            "citations": [
                {
                    "source_id": "LS-FABRICATED",
                    "article_number": "第一条",
                    "text": "虚构条文",
                    "official_source_url": None,
                    "version_hash": None,
                },
                {
                    "source_id": hit.source_id,
                    "article_number": "第二百六十五条",
                    "text": "被篡改的原文",
                    "official_source_url": "https://example.invalid/",
                    "version_hash": "b" * 64,
                },
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["results"][0]["failures"] == ["SOURCE_NOT_RETRIEVED"]
    assert set(body["results"][1]["failures"]) == {
        "ARTICLE_NUMBER_MISMATCH",
        "TEXT_MISMATCH",
        "OFFICIAL_SOURCE_MISMATCH",
        "VERSION_HASH_MISMATCH",
    }


async def test_unknown_legal_trace_returns_404(legal_api_client: AsyncClient) -> None:
    trace_response = await legal_api_client.get("/api/v1/legal/search-traces/missing")
    citation_response = await legal_api_client.post(
        "/api/v1/legal/citations/validate",
        json={
            "trace_id": "missing",
            "citations": [
                {
                    "source_id": "LS-FABRICATED",
                    "article_number": "第一条",
                    "text": "虚构条文",
                    "official_source_url": None,
                    "version_hash": None,
                }
            ],
        },
    )

    assert trace_response.status_code == 404
    assert citation_response.status_code == 404
    assert trace_response.json()["detail"]["code"] == "legal_search_trace_not_found"
