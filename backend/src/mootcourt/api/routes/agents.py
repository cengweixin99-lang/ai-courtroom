from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status

from mootcourt.api.dependencies import RuntimeAgentProvider, RuntimeUnitOfWork
from mootcourt.core.config import Settings, get_settings
from mootcourt.schemas.agents import AgentTraceStatus, AgentTraceView, AgentTurnRequest
from mootcourt.schemas.runtime import AgentTurnResponse, ParticipantStatementTraceView
from mootcourt.services.agent_turns import (
    AgentTurnServiceError,
    execute_agent_turn,
    list_agent_traces,
    list_participant_statement_traces,
)

router = APIRouter(prefix="/sessions", tags=["agents"])
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/{session_id}/agent-turns",
    response_model=AgentTurnResponse,
    operation_id="execute_agent_turn",
    summary="执行一次受控 Agent 回合",
    response_description="Agent 输出、庭审事件、会话状态和调用 Trace",
    responses={
        403: {"description": "Agent 输出引用当前角色无权访问的材料"},
        404: {"description": "庭审会话或参与人不存在"},
        409: {"description": "角色、动作或会话状态不允许本次调用"},
        429: {"description": "会话 Token、成本或时间预算已耗尽"},
        422: {"description": "请求参数或参与人类型无效"},
        502: {"description": "模型调用或结构化输出校验失败；失败 Trace 已保存"},
        503: {"description": "真实模型 Provider 未正确配置"},
    },
)
async def run_agent_turn(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    request: AgentTurnRequest,
    response: Response,
    unit_of_work: RuntimeUnitOfWork,
    provider: RuntimeAgentProvider,
    settings: AppSettings,
) -> AgentTurnResponse:
    """执行由状态机约束的单次 Agent 调用。

    客户端可以指定希望调用的系统角色和动作，但 Service 会根据会话用户角色、当前阶段、
    参与人类型和证据权限重新验证。失败调用保存 Trace，但不会写入庭审事件。
    """
    try:
        result = await execute_agent_turn(unit_of_work, session_id, request, provider, settings)
    except AgentTurnServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    if result.status is AgentTraceStatus.FAILED:
        # 不抛异常，使依赖层可以提交失败 Trace；预算失败使用 429，其余上游失败使用 502。
        error_code = result.error.code if result.error is not None else ""
        response.status_code = (
            status.HTTP_429_TOO_MANY_REQUESTS
            if error_code.startswith("session_") and error_code.endswith("_budget_exceeded")
            else status.HTTP_502_BAD_GATEWAY
        )
    return result


@router.get(
    "/{session_id}/traces",
    response_model=list[AgentTraceView],
    operation_id="list_agent_traces",
    summary="获取会话 Agent 调用 Trace",
    response_description="按创建时间排列的 Agent 调用诊断元数据",
    responses={404: {"description": "庭审会话不存在"}},
)
async def get_agent_traces(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    unit_of_work: RuntimeUnitOfWork,
) -> list[AgentTraceView]:
    """返回调用状态、模型、Token、延迟、成本和错误，不暴露完整提示词快照。"""
    traces = await list_agent_traces(unit_of_work, session_id)
    if traces is None:
        raise HTTPException(status_code=404, detail={"code": "session_not_found"})
    return traces


@router.get(
    "/{session_id}/participant-statement-traces",
    response_model=list[ParticipantStatementTraceView],
    operation_id="list_participant_statement_traces",
    summary="获取参与人陈述一致性留痕",
    response_description="证人或被告人回答引用的既有陈述、关联事实及确定性分类",
    responses={404: {"description": "庭审会话不存在"}},
)
async def get_participant_statement_traces(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    unit_of_work: RuntimeUnitOfWork,
) -> list[ParticipantStatementTraceView]:
    """返回可审计的一致性记录；系统不以字符串规则推断语义矛盾。"""
    traces = await list_participant_statement_traces(unit_of_work, session_id)
    if traces is None:
        raise HTTPException(status_code=404, detail={"code": "session_not_found"})
    return traces
