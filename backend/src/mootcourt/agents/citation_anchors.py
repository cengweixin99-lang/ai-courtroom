from __future__ import annotations

from dataclasses import dataclass

from mootcourt.schemas.agents import AgentContext, AgentEvidenceContext

_MIN_QUOTE_LENGTH = 6
_MAX_QUOTE_LENGTH = 500
_PREFERRED_BOUNDARIES = "。；！？\n"


@dataclass(frozen=True, slots=True)
class EvidenceCitationAnchor:
    """由程序生成的证据原文锚点，模型只能选择，不能改写。"""

    anchor_id: str
    evidence_id: str
    quote: str


def build_evidence_citation_anchors(context: AgentContext) -> tuple[EvidenceCitationAnchor, ...]:
    allowed_ids = set(context.task.evidence_ids) or {item.id for item in context.evidence}
    anchors: list[EvidenceCitationAnchor] = []
    for evidence in context.evidence:
        if evidence.id not in allowed_ids:
            continue
        anchors.extend(_evidence_anchors(evidence))
    return tuple(anchors)


def _evidence_anchors(evidence: AgentEvidenceContext) -> list[EvidenceCitationAnchor]:
    anchors: list[EvidenceCitationAnchor] = []
    sources = [
        ("content", evidence.content),
        *(
            (f"reliability-{index}", note)
            for index, note in enumerate(evidence.reliability_notes, start=1)
        ),
    ]
    for source_name, source in sources:
        for chunk_index, quote in enumerate(_source_chunks(source), start=1):
            anchors.append(
                EvidenceCitationAnchor(
                    anchor_id=f"{evidence.id}:{source_name}:{chunk_index}",
                    evidence_id=evidence.id,
                    quote=quote,
                )
            )
    return anchors


def _source_chunks(source: str) -> list[str]:
    """长原文按可读边界切成不超过 Schema 上限的连续片段。"""

    remaining = source.strip()
    chunks: list[str] = []
    while len(remaining) > _MAX_QUOTE_LENGTH:
        window = remaining[:_MAX_QUOTE_LENGTH]
        boundary = max(window.rfind(item) for item in _PREFERRED_BOUNDARIES)
        split_at = boundary + 1 if boundary + 1 >= _MIN_QUOTE_LENGTH else _MAX_QUOTE_LENGTH
        chunk = remaining[:split_at].strip()
        if len(chunk) >= _MIN_QUOTE_LENGTH:
            chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()
    if len(remaining) >= _MIN_QUOTE_LENGTH:
        chunks.append(remaining)
    return chunks
