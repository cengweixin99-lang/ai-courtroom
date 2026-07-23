from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path

from mootcourt.api.dependencies import (
    RuntimeCurrentUser,
    RuntimeDiagnosticsAccess,
    RuntimeLegalEmbeddingProvider,
    RuntimeLegalSearchRepository,
    RuntimeUnitOfWork,
    require_authenticated_principal,
)
from mootcourt.schemas.legal_search import (
    LegalCitationValidationRequest,
    LegalCitationValidationResponse,
    LegalSearchRequest,
    LegalSearchResponse,
    LegalSearchTraceView,
)
from mootcourt.services.legal_citations import get_legal_search_trace, validate_legal_citations
from mootcourt.services.legal_search import search_case_law

router = APIRouter(
    prefix="/legal",
    tags=["legal-search"],
    dependencies=[Depends(require_authenticated_principal)],
)


@router.post(
    "/search",
    response_model=LegalSearchResponse,
    operation_id="search_case_law",
    summary="检索案件适用的候选法律依据",
    response_description="经过法域、版本、效力和案件来源白名单过滤的候选条款",
    responses={
        404: {"description": "案件或指定案件包版本不存在"},
        503: {"description": "法律索引尚未建立或 Elasticsearch 不可用"},
    },
)
async def search_legal_authority(
    request: LegalSearchRequest,
    unit_of_work: RuntimeUnitOfWork,
    search_repository: RuntimeLegalSearchRepository,
    embedding_provider: RuntimeLegalEmbeddingProvider,
    current_user: RuntimeCurrentUser,
) -> LegalSearchResponse:
    """基于案件锁定的 LegalProfile 执行 BM25 或 BM25 + 向量混合检索。

    客户端只能提交单一法律问题和返回数量，不能自行指定法域、生效日期或放宽来源白名单。
    向量检索仅在服务端显式配置并完成同版本索引后启用。本接口只返回候选依据，不执行
    构成要件判断，也不生成法律结论。
    """
    package = await unit_of_work.case_packages.get_runtime_package(request.case_id)
    if package is None or not await unit_of_work.identity.can_access_case(
        current_user.id, package.id
    ):
        raise HTTPException(status_code=404, detail={"code": "case_not_found"})
    try:
        result = await search_case_law(unit_of_work, search_repository, request, embedding_provider)
    except Exception as exc:
        # 搜索基础设施失败不能降级为模型凭记忆作答。
        raise HTTPException(
            status_code=503,
            detail={
                "code": "legal_search_unavailable",
                "message": "legal search service is unavailable",
            },
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "case_not_found"})
    return result


@router.get(
    "/search-traces/{trace_id}",
    response_model=LegalSearchTraceView,
    operation_id="get_legal_search_trace",
    summary="获取法律检索审计 Trace",
    response_description="固定过滤条件、候选法源、检索分数、模型版本和耗时",
    responses={
        401: {"description": "生产环境需要诊断访问凭据"},
        404: {"description": "法律检索 Trace 不存在"},
    },
)
async def get_search_trace(
    trace_id: Annotated[str, Path(description="法律检索 Trace 唯一标识")],
    unit_of_work: RuntimeUnitOfWork,
    _: RuntimeDiagnosticsAccess,
) -> LegalSearchTraceView:
    """返回可复现检索审计信息，不包含 embedding 数组、密钥或数据库连接信息。"""
    result = await get_legal_search_trace(unit_of_work, trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "legal_search_trace_not_found"})
    return result


@router.post(
    "/citations/validate",
    response_model=LegalCitationValidationResponse,
    operation_id="validate_legal_citations",
    summary="校验法律引用真实性",
    response_description="每条引用与检索 Trace 原文及版本元数据的严格比对结果",
    responses={404: {"description": "法律检索 Trace 不存在"}},
)
async def validate_citations(
    request: LegalCitationValidationRequest,
    unit_of_work: RuntimeUnitOfWork,
) -> LegalCitationValidationResponse:
    """只认可该 Trace 实际召回且逐字段一致的候选，禁止伪造或篡改引用。"""
    result = await validate_legal_citations(unit_of_work, request)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "legal_search_trace_not_found"})
    return result
