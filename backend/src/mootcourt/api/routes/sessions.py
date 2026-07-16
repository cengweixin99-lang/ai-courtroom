from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from mootcourt.api.dependencies import RuntimeUnitOfWork
from mootcourt.core.config import Settings, get_settings
from mootcourt.schemas.runtime import (
    SessionActionRequest,
    SessionActionResponse,
    SessionCreate,
    SessionEventView,
    SessionView,
)
from mootcourt.services.court_sessions import (
    SessionServiceError,
    apply_session_action,
    create_court_session,
    get_session_view,
    list_session_events,
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
