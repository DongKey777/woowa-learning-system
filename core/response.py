"""Parse AI session response → state updates + UX helpers.

The AI session writes its learner-facing answer in markdown. This module:
- extracts `참고:\\n- <id>\\n- <id>` citations
- extracts mastery/uncertainty signals declared by the AI ("mastered: foo,bar")
- normalizes the [RAG: tier-N — reason · 적용: ...] header
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CITATION_BLOCK = re.compile(r"참고\s*:\s*\n((?:\s*-\s*[^\n]+\n?)+)", re.MULTILINE)
HEADER_PATTERN = re.compile(r"\[RAG:\s*tier-(?P<tier>\d)\s*—\s*(?P<reason>[^\]\·]+?)(?:\s*·\s*적용:\s*(?P<tags>[^\]]+))?\]")
MASTERED_HINT = re.compile(r"mastered\s*:\s*([^\n]+)", re.IGNORECASE)
UNCERTAIN_HINT = re.compile(r"uncertain\s*:\s*([^\n]+)", re.IGNORECASE)


@dataclass(frozen=True)
class ResponseTrace:
    tier: int | None
    reason: str | None
    applied_tags: list[str]
    citations: list[str]
    declared_mastered: list[str]
    declared_uncertain: list[str]
    has_rag_header: bool
    has_citation_block: bool


def parse_response(answer_md: str) -> ResponseTrace:
    citations = _extract_citations(answer_md)
    tier, reason, tags = _extract_header(answer_md)
    mastered = _extract_csv(MASTERED_HINT, answer_md)
    uncertain = _extract_csv(UNCERTAIN_HINT, answer_md)
    return ResponseTrace(
        tier=tier,
        reason=reason,
        applied_tags=tags,
        citations=citations,
        declared_mastered=mastered,
        declared_uncertain=uncertain,
        has_rag_header=tier is not None,
        has_citation_block=bool(citations),
    )


def quality_flags(trace: ResponseTrace, expected_citations: list[str]) -> list[str]:
    """Return list of flag strings for telemetry (matches CLAUDE.md flags)."""
    flags: list[str] = []
    if not trace.has_rag_header:
        flags.append("missing_rag_header")
    if expected_citations and not trace.citations:
        flags.append("missing_citation")
    elif expected_citations and trace.citations and set(trace.citations).isdisjoint(expected_citations):
        flags.append("citation_mismatch")
    return flags


def render_citation_block(concept_ids: list[str], max_n: int = 3) -> str:
    if not concept_ids:
        return ""
    head = "참고:\n"
    body = "\n".join(f"- {cid}" for cid in concept_ids[:max_n])
    return head + body


def _extract_citations(text: str) -> list[str]:
    m = CITATION_BLOCK.search(text)
    if not m:
        return []
    block = m.group(1)
    out: list[str] = []
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("-"):
            cid = line.lstrip("- ").strip()
            if cid:
                out.append(cid)
    return out


def _extract_header(text: str) -> tuple[int | None, str | None, list[str]]:
    m = HEADER_PATTERN.search(text)
    if not m:
        return None, None, []
    tier = int(m.group("tier"))
    reason = m.group("reason").strip()
    raw_tags = m.group("tags") or ""
    tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
    return tier, reason, tags


def _extract_csv(pattern: re.Pattern, text: str) -> list[str]:
    m = pattern.search(text)
    if not m:
        return []
    return [t.strip() for t in m.group(1).split(",") if t.strip()]
