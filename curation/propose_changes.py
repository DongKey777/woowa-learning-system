"""Convert MinedSignal → AI session prompt → corpus change proposals.

AI session reads `build_proposal_prompt(signals, concept_snippets)`, emits
JSON proposals matching ProposedChange schema, then curation/apply_changes
mutates corpus.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from curation.mine_history import MinedSignal


@dataclass(frozen=True)
class ProposedChange:
    target_concept_id: str
    operation: str  # "add_alias" | "add_expected_query" | "add_symptom" | "add_confusable" | "new_concept"
    payload: dict
    rationale: str  # AI session's justification


def build_proposal_prompt(signals: list[MinedSignal], concept_snippets: dict[str, dict]) -> str:
    """Render Korean prompt for AI session to emit JSON proposals."""
    lines = [
        "# Corpus curation proposals",
        "",
        "다음 signals를 보고, 각 signal에 대한 corpus 변경 제안을 JSON으로 emit.",
        "",
        "## Mined signals",
    ]
    for s in signals:
        lines.append(f"### {s.pattern} — target=`{s.target_concept_id or '(미정)'}` (freq={s.frequency})")
        if s.queries:
            lines.append("Queries (앞 5개):")
            for q in s.queries[:5]:
                lines.append(f"  - {q}")
        if s.target_concept_id and s.target_concept_id in concept_snippets:
            snip = concept_snippets[s.target_concept_id]
            lines.append("Current concept snapshot:")
            lines.append(
                f"  - aliases: {snip.get('aliases')}\n"
                f"  - expected_queries: {snip.get('expected_queries')}\n"
                f"  - summary: {snip.get('summary', '')[:120]}…"
            )
        lines.append("")

    lines.extend([
        "## 출력 형식 (JSON, 1 array)",
        "```json",
        "[",
        '  {"target_concept_id": "...", "operation": "add_alias|add_expected_query|add_symptom|add_confusable|new_concept",',
        '   "payload": { ... }, "rationale": "<한 줄>"},',
        "  ...",
        "]",
        "```",
        "",
        "예시 payload:",
        '- add_alias: `{"alias": "스프링빈"}`',
        '- add_expected_query: `{"query": "Bean이 뭐야"}`',
        '- add_symptom: `{"symptom": "NullPointerException at @Autowired"}`',
        '- add_confusable: `{"with": "spring/component"}`',
        '- new_concept: `{"id": "spring/...", "title": "...", "summary": "...", "body_markdown": "..."}`',
    ])
    return "\n".join(lines)


def parse_proposals(json_text: str) -> list[ProposedChange]:
    """Extract proposals from AI session JSON output (tolerates ```json fence)."""
    text = json_text.strip()
    if text.startswith("```"):
        # strip fence
        text = "\n".join(line for line in text.splitlines() if not line.startswith("```"))
    arr = json.loads(text)
    out: list[ProposedChange] = []
    for item in arr:
        out.append(ProposedChange(
            target_concept_id=item["target_concept_id"],
            operation=item["operation"],
            payload=item["payload"],
            rationale=item.get("rationale", ""),
        ))
    return out
