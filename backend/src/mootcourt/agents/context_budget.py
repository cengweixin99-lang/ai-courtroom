from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from mootcourt.schemas.agents import AgentContext


class ContextBudgetExceeded(ValueError):
    """强制保留的案件材料本身已经超过模型输入预算。"""


@dataclass(frozen=True, slots=True)
class ContextBudgetReport:
    original_tokens: int
    final_tokens: int
    removed_event_count: int
    removed_evidence_ids: tuple[str, ...]
    removed_fact_ids: tuple[str, ...]
    removed_statement_ids: tuple[str, ...]
    summary_removed: bool


def estimate_text_tokens(text: str) -> int:
    """跨 OpenAI-compatible 模型使用的保守估算，不依赖某一家 Provider tokenizer。"""
    return max(1, math.ceil(len(text.encode("utf-8")) / 3))


def fit_agent_context(
    context: AgentContext,
    *,
    instruction: str | None,
    max_tokens: int,
    measure: Callable[[AgentContext], int],
) -> tuple[AgentContext, ContextBudgetReport]:
    working = context.model_copy(deep=True)
    original_tokens = measure(working)
    removed_evidence: list[str] = []
    removed_facts: list[str] = []
    removed_statements: list[str] = []
    removed_events = 0
    summary_removed = False

    if original_tokens <= max_tokens:
        return working, ContextBudgetReport(
            original_tokens, original_tokens, 0, (), (), (), False
        )

    # 历史事件可由数据库重建，优先从最旧记录开始移除。
    while working.recent_events and measure(working) > max_tokens:
        working.recent_events.pop(0)
        removed_events += 1

    task_evidence_ids = set(working.task.evidence_ids)
    while working.role_materials and measure(working) > max_tokens:
        working.role_materials.pop()

    # 本轮明确指定的证据不可裁剪；未指定时至少保留一项，避免失去事实锚点。
    removable_evidence = [
        item.id
        for item in reversed(working.evidence)
        if item.id not in task_evidence_ids
    ]
    minimum_evidence = (
        len(task_evidence_ids) if task_evidence_ids else min(1, len(working.evidence))
    )
    for evidence_id in removable_evidence:
        if measure(working) <= max_tokens or len(working.evidence) <= minimum_evidence:
            break
        working.evidence = [item for item in working.evidence if item.id != evidence_id]
        removed_evidence.append(evidence_id)

    retained_evidence_ids = {item.id for item in working.evidence}
    removable_facts = [
        item.id
        for item in reversed(working.facts)
        if not retained_evidence_ids.intersection(
            item.supporting_evidence_ids + item.contradicting_evidence_ids
        )
    ]
    for fact_id in removable_facts:
        if measure(working) <= max_tokens:
            break
        working.facts = [item for item in working.facts if item.id != fact_id]
        removed_facts.append(fact_id)

    participant = working.participant
    if participant is not None and len(participant.statements) > 1:
        ranked = sorted(
            participant.statements,
            key=lambda item: (_relevance(item.text, instruction), item.id),
        )
        for statement in ranked[:-1]:
            if measure(working) <= max_tokens:
                break
            participant.statements = [
                item for item in participant.statements if item.id != statement.id
            ]
            removed_statements.append(statement.id)

    if measure(working) > max_tokens and working.case.summary:
        working.case.summary = ""
        summary_removed = True

    final_tokens = measure(working)
    if final_tokens > max_tokens:
        raise ContextBudgetExceeded(
            f"mandatory Agent context requires approximately {final_tokens} tokens; "
            f"configured limit is {max_tokens}"
        )
    return working, ContextBudgetReport(
        original_tokens=original_tokens,
        final_tokens=final_tokens,
        removed_event_count=removed_events,
        removed_evidence_ids=tuple(removed_evidence),
        removed_fact_ids=tuple(removed_facts),
        removed_statement_ids=tuple(removed_statements),
        summary_removed=summary_removed,
    )


def _relevance(text: str, instruction: str | None) -> int:
    if not instruction:
        return 0
    terms = {char for char in instruction if char.isalnum()}
    return sum(1 for char in text if char in terms)
