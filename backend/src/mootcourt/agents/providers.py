from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from mootcourt.schemas.agents import (
    AgentContext,
    AgentOutputKind,
    AgentRole,
    Certainty,
)


@dataclass(frozen=True, slots=True)
class AgentProviderRequest:
    context: AgentContext
    instruction: str | None
    repair_instruction: str | None = None


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
        return AgentProviderResult(
            output=output,
            provider=self.provider_name,
            model=self.model_name,
        )

    @staticmethod
    def _advocate_output(context: AgentContext) -> dict[str, Any]:
        evidence_ids = [item.id for item in context.evidence[:1]]
        claims = []
        if evidence_ids:
            claims.append(
                {
                    "text": "本方依据当前角色有权访问的案卷材料提出该项主张。",
                    "claim_type": "supported_fact",
                    "evidence_ids": evidence_ids,
                }
            )
        return {
            "kind": AgentOutputKind.ADVOCATE.value,
            "speaker_role": context.actor_role.value,
            "speech": "本方已根据当前庭审阶段和可见材料完成陈述。",
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
                "certainty": Certainty.LOW.value,
                "refused_reason": "没有可追溯的既有陈述",
            }
        statement = participant.statements[0]
        return {
            "kind": AgentOutputKind.WITNESS.value,
            "answer": statement.text,
            "supported_by_statement_ids": [statement.id],
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
                "new_statement": False,
                "certainty": Certainty.LOW.value,
                "refused_reason": "没有可追溯的既有陈述",
            }
        statement = participant.statements[0]
        return {
            "kind": AgentOutputKind.DEFENDANT.value,
            "answer": statement.text,
            "supported_by_statement_ids": [statement.id],
            "new_statement": False,
            "certainty": statement.certainty,
            "refused_reason": None,
        }
