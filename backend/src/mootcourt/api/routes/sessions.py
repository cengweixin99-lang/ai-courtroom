from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from mootcourt.api.dependencies import RuntimeUnitOfWork
from mootcourt.core.config import Settings, get_settings
from mootcourt.schemas.reviews import (
    CourtReviewGenerateRequest,
    CourtReviewReport,
    NewStatementResolutionRequest,
    NewStatementResolutionResponse,
)
from mootcourt.schemas.runtime import (
    EvidenceFactSummaryView,
    EvidenceStatusView,
    ProceduralRequestResolutionRequest,
    ProceduralRequestResolutionResponse,
    ProceduralRequestView,
    SessionActionRequest,
    SessionActionResponse,
    SessionCreate,
    SessionEventView,
    SessionView,
)
from mootcourt.services.court_reviews import (
    CourtReviewServiceError,
    generate_court_review,
    get_court_review,
    resolve_new_statement,
)
from mootcourt.services.court_sessions import (
    SessionServiceError,
    apply_session_action,
    create_court_session,
    get_evidence_fact_summary,
    get_session_view,
    list_evidence_statuses,
    list_procedural_requests,
    list_session_events,
    resolve_procedural_request,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.post(
    "",
    response_model=SessionView,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_court_session",
    summary="创建庭审会话",
    response_description="已创建并锁定案件包版本的庭审会话",
    responses={404: {"description": "案件或指定版本不存在"}},
)
async def create_session(request: SessionCreate, unit_of_work: RuntimeUnitOfWork) -> SessionView:
    """创建绑定案件版本和用户角色的庭审会话。

    会话和首条事件由同一个 Unit of Work 原子提交，避免出现缺少审计起点的会话。
    """
    try:
        session_id = await create_court_session(unit_of_work, request)
    except SessionServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
        ) from exc
    # 复用标准查询路径，避免字段演进后创建接口和查询接口的响应结构发生偏差。
    view = await get_session_view(unit_of_work, session_id)
    if view is None:
        raise HTTPException(status_code=500, detail={"code": "session_creation_failed"})
    return view


@router.get(
    "/{session_id}",
    response_model=SessionView,
    operation_id="get_court_session",
    summary="获取庭审会话状态",
    response_description="会话当前阶段、可执行动作和已提交证据",
    responses={404: {"description": "庭审会话不存在"}},
)
async def get_session(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    unit_of_work: RuntimeUnitOfWork,
) -> SessionView:
    """恢复会话的当前状态，供页面刷新或中断后继续庭审。"""
    view = await get_session_view(unit_of_work, session_id)
    if view is None:
        raise HTTPException(status_code=404, detail={"code": "session_not_found"})
    return view


@router.get(
    "/{session_id}/events",
    response_model=list[SessionEventView],
    operation_id="list_court_session_events",
    summary="获取庭审事件日志",
    response_description="按序号升序排列的不可变庭审事件",
    responses={404: {"description": "庭审会话不存在"}},
)
async def get_events(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    unit_of_work: RuntimeUnitOfWork,
) -> list[SessionEventView]:
    """返回有序事件日志，用于庭审回放、审计和故障排查。"""
    events = await list_session_events(unit_of_work, session_id)
    if events is None:
        raise HTTPException(status_code=404, detail={"code": "session_not_found"})
    return events


@router.get(
    "/{session_id}/evidence-statuses",
    response_model=list[EvidenceStatusView],
    operation_id="list_session_evidence_statuses",
    summary="获取会话证据状态台账",
    response_description="证据可见性、未提交或已提交状态及提交信息",
    responses={404: {"description": "庭审会话不存在"}},
)
async def get_evidence_statuses(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    unit_of_work: RuntimeUnitOfWork,
) -> list[EvidenceStatusView]:
    """返回确定性证据状态，不暴露当前角色无权访问的证据正文。"""
    result = await list_evidence_statuses(unit_of_work, session_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "session_not_found"})
    return result


@router.get(
    "/{session_id}/procedural-requests",
    response_model=list[ProceduralRequestView],
    operation_id="list_session_procedural_requests",
    summary="获取会话程序请求和质证记录",
    response_description="问题制止请求、证据质证维度及处理状态",
    responses={404: {"description": "庭审会话不存在"}},
)
async def get_procedural_requests(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    unit_of_work: RuntimeUnitOfWork,
) -> list[ProceduralRequestView]:
    """返回已写入公开庭审记录的结构化程序请求，不调用模型生成裁定。"""
    result = await list_procedural_requests(unit_of_work, session_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "session_not_found"})
    return result


@router.post(
    "/{session_id}/procedural-requests/{request_id}/resolution",
    response_model=ProceduralRequestResolutionResponse,
    operation_id="resolve_session_procedural_request",
    summary="处理会话程序请求",
    response_description="教学控制者处理结果及对应公开庭审事件",
    responses={
        404: {"description": "庭审会话或当前会话内的程序请求不存在"},
        409: {"description": "程序请求已经处理"},
        422: {"description": "处理结果与程序请求类型不匹配"},
    },
)
async def submit_procedural_request_resolution(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    request_id: Annotated[str, Path(description="程序请求唯一标识")],
    request: ProceduralRequestResolutionRequest,
    unit_of_work: RuntimeUnitOfWork,
) -> ProceduralRequestResolutionResponse:
    """由教学流程控制者处理程序请求；当前版本尚未接入真实身份认证。"""
    try:
        return await resolve_procedural_request(unit_of_work, session_id, request_id, request)
    except SessionServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
        ) from exc


@router.get(
    "/{session_id}/evidence-fact-summary",
    response_model=list[EvidenceFactSummaryView],
    operation_id="get_session_evidence_fact_summary",
    summary="获取证据使用与事实支持汇总",
    response_description="案卷事实的关联证据、提交情况及已出现陈述，不作事实认定",
    responses={404: {"description": "庭审会话不存在"}},
)
async def get_session_evidence_fact_summary(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    unit_of_work: RuntimeUnitOfWork,
) -> list[EvidenceFactSummaryView]:
    """提供确定性的材料使用汇总；support_status 不表示法院已经认定事实成立。"""
    result = await get_evidence_fact_summary(unit_of_work, session_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "session_not_found"})
    return result


@router.post(
    "/{session_id}/participant-statement-traces/{trace_id}/resolution",
    response_model=NewStatementResolutionResponse,
    operation_id="resolve_session_new_statement",
    summary="审核本庭新增陈述",
    response_description="教学控制者决定是否将新增陈述纳入本庭记录",
    responses={
        404: {"description": "会话或陈述留痕不存在"},
        409: {"description": "新增陈述已经审核"},
        422: {"description": "该留痕不是本庭新增陈述"},
    },
)
async def submit_new_statement_resolution(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    trace_id: Annotated[str, Path(description="参与人陈述留痕唯一标识")],
    request: NewStatementResolutionRequest,
    unit_of_work: RuntimeUnitOfWork,
) -> NewStatementResolutionResponse:
    """审核只决定陈述是否进入庭审记录，不自动建立事实支持关系。"""
    try:
        return await resolve_new_statement(unit_of_work, session_id, trace_id, request)
    except CourtReviewServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
        ) from exc


@router.post(
    "/{session_id}/review",
    response_model=CourtReviewReport,
    operation_id="generate_session_court_review",
    summary="生成结构化教学复盘",
    response_description="基于公开庭审材料、冻结构成要件和已核验法源的结构化复盘",
    responses={
        404: {"description": "庭审会话不存在"},
        409: {"description": "阶段不允许、审核未完成或复盘已经存在"},
        422: {"description": "法律 Trace 不属于本案或必要法源不足"},
    },
)
async def create_session_court_review(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    request: CourtReviewGenerateRequest,
    unit_of_work: RuntimeUnitOfWork,
) -> CourtReviewReport:
    """不调用自由裁判 Agent；每个构成要件必须具有可核验法源和本庭事实状态。"""
    try:
        return await generate_court_review(unit_of_work, session_id, request)
    except CourtReviewServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
        ) from exc


@router.get(
    "/{session_id}/review",
    response_model=CourtReviewReport,
    operation_id="get_session_court_review",
    summary="获取结构化教学复盘",
    response_description="会话已生成的事实、构成要件和法源可追溯报告",
    responses={404: {"description": "会话或复盘不存在"}},
)
async def get_session_court_review(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    unit_of_work: RuntimeUnitOfWork,
) -> CourtReviewReport:
    """返回持久化复盘快照，避免后续案卷版本变化影响既有教学记录。"""
    result = await get_court_review(unit_of_work, session_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "court_review_not_found"})
    return result


@router.post(
    "/{session_id}/actions",
    response_model=SessionActionResponse,
    operation_id="apply_court_session_action",
    summary="执行庭审动作",
    response_description="执行后的会话状态、持久化事件和固定反馈",
    responses={
        403: {"description": "当前角色不可访问所引用的证据"},
        404: {"description": "庭审会话不存在"},
        409: {"description": "动作不符合阶段、角色或当前会话状态"},
        422: {"description": "请求字段或动作业务参数无效"},
    },
)
async def submit_action(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    request: SessionActionRequest,
    unit_of_work: RuntimeUnitOfWork,
    settings: AppSettings,
) -> SessionActionResponse:
    """校验并记录一次确定性的庭审动作。

    动作执行者取自已持久化的会话角色，客户端不能伪造。系统在写事件前依次校验
    阶段与角色、证据可见性、重复提交、质证前置提交、参与人存在性和回合上限。
    E1 只返回固定反馈，不调用大语言模型。
    """
    try:
        return await apply_session_action(unit_of_work, session_id, request, settings)
    except SessionServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
        ) from exc
