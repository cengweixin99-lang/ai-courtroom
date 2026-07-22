from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from mootcourt.domain.courtroom import CourtAction
from mootcourt.schemas.agents import (
    AgentContext,
    AgentOutputKind,
    AgentRole,
    Certainty,
)

TextUpdateCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AgentProviderRequest:
    context: AgentContext
    instruction: str | None
    repair_instruction: str | None = None
    on_text_update: TextUpdateCallback | None = None


@dataclass(frozen=True, slots=True)
class StructuredProviderRequest:
    """供教学评审等非庭审角色任务使用的受限 JSON 请求。"""

    messages: tuple[dict[str, str], ...]
    schema_name: str
    response_schema: dict[str, Any]
    fallback_output: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AgentProviderResult:
    output: dict[str, Any]
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_cny: float = 0


class AgentProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult: ...



class StructuredAgentProvider(AgentProvider, Protocol):
    async def generate_structured(
        self, request: StructuredProviderRequest
    ) -> AgentProviderResult: ...


class FakeAgentProvider:
    """开发阶段的确定性 Provider，用于验证编排与安全边界，不模拟模型能力。"""

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-e2.1"

    async def generate(self, request: AgentProviderRequest) -> AgentProviderResult:
        context = request.context
        if context.actor_role in {AgentRole.PROSECUTION, AgentRole.DEFENSE}:
            output = self._advocate_output(context)
        elif context.actor_role is AgentRole.WITNESS:
            output = self._witness_output(context)
        else:
            output = self._defendant_output(context)
        if request.on_text_update is not None:
            visible_text = output.get("speech") or output.get("answer")
            if isinstance(visible_text, str):
                await request.on_text_update(visible_text)
        return AgentProviderResult(
            output=output,
            provider=self.provider_name,
            model=self.model_name,
        )

    async def generate_structured(self, request: StructuredProviderRequest) -> AgentProviderResult:
        """测试环境使用调用方提供的确定性样例，避免伪造模型评审能力。"""

        return AgentProviderResult(
            output=request.fallback_output,
            provider=self.provider_name,
            model=self.model_name,
        )

    @staticmethod
    def _advocate_output(context: AgentContext) -> dict[str, Any]:
        if context.action is CourtAction.QUESTION_PARTICIPANT:
            return {
                "kind": AgentOutputKind.ADVOCATE.value,
                "speaker_role": context.actor_role.value,
                "speech": "请根据你亲历并已经陈述的内容回答本次问题。",
                "claims": [],
                "requested_action": context.action.value,
                "target_id": context.participant.id if context.participant is not None else None,
            }
        task_evidence_ids = set(context.task.evidence_ids)
        selected = [
            item
            for item in context.evidence
            if not task_evidence_ids or item.id in task_evidence_ids
        ]
        claims = [
            {
                "text": f"本方依据证据 {item.id} 提出该项主张。",
                "claim_type": "supported_fact",
                "fact_ids": [
                    fact_id
                    for fact_id in item.related_fact_ids
                    if any(
                        fact.id == fact_id and item.id in fact.supporting_evidence_ids
                        for fact in context.facts
                    )
                ][:1],
                "citations": [
                    {
                        "evidence_id": item.id,
                        "quote": item.content[:500],
                    }
                ],
            }
            for item in selected[: max(1, len(task_evidence_ids))]
            if any(
                fact.id in item.related_fact_ids and item.id in fact.supporting_evidence_ids
                for fact in context.facts
            )
        ]
        speech = "".join(str(item["text"]) for item in claims) or "本方完成本次程序性发言。"
        return {
            "kind": AgentOutputKind.ADVOCATE.value,
            "speaker_role": context.actor_role.value,
            "speech": speech,
            "claims": claims,
            "requested_action": context.action.value,
            "target_id": context.participant.id if context.participant is not None else None,
        }

    @staticmethod
    def _witness_output(context: AgentContext) -> dict[str, Any]:
        participant = context.participant
        if participant is None or not participant.statements:
            return {
                "kind": AgentOutputKind.WITNESS.value,
                "answer": "现有陈述记录不足，我无法回答。",
                "supported_by_statement_ids": [],
                "citations": [],
                "certainty": Certainty.LOW.value,
                "refused_reason": "没有可追溯的既有陈述",
            }
        statement = participant.statements[0]
        return {
            "kind": AgentOutputKind.WITNESS.value,
            "answer": statement.text,
            "supported_by_statement_ids": [statement.id],
            "citations": [{"statement_id": statement.id, "quote": statement.text[:500]}],
            "certainty": statement.certainty,
            "refused_reason": None,
        }

    @staticmethod
    def _defendant_output(context: AgentContext) -> dict[str, Any]:
        participant = context.participant
        if participant is None or not participant.statements:
            return {
                "kind": AgentOutputKind.DEFENDANT.value,
                "answer": "现有陈述记录不足，我无法回答。",
                "supported_by_statement_ids": [],
                "citations": [],
                "new_statement": False,
                "certainty": Certainty.LOW.value,
                "refused_reason": "没有可追溯的既有陈述",
            }
        statement = participant.statements[0]
        return {
            "kind": AgentOutputKind.DEFENDANT.value,
            "answer": statement.text,
            "supported_by_statement_ids": [statement.id],
            "citations": [{"statement_id": statement.id, "quote": statement.text[:500]}],
            "new_statement": False,
            "certainty": statement.certainty,
            "refused_reason": None,
        }
