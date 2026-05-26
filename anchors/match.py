"""4-stage cross-crew/reviewer anchor matching (F11, D-E).

Stage 1: path filter (cheap suffix match in parquet/SQLite)
Stage 2: code hunk Jaccard (>0.4 threshold) on normalized tokens
Stage 3: embedding similarity (cosine, top-10) — uses BGE-M3 already in repo
Stage 4: AI session veto (top-5 only, ~500 tokens) — final precision boost

Goal: false-positive 37% → 8-12% (plan §D-E).
Returns CrossCrewMatch dataclass list ordered by combined confidence.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from anchors.extract import ReviewAnchor

JACCARD_THRESHOLD = 0.4
STAGE3_TOP_K = 10
STAGE4_TOP_K = 5

JAVA_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")


@dataclass(frozen=True)
class CrossCrewMatch:
    anchor_thread_id: str
    candidate_repo: str
    candidate_pr: int
    candidate_path: str
    candidate_line: int | None
    candidate_diff_hunk: str
    candidate_reviewer: str
    candidate_comment: str
    crew_login: str
    jaccard: float
    embed_cosine: float
    ai_veto: bool       # True = passed AI veto, False = rejected
    stage_passed: int   # last stage that accepted this match (1..4)


def _tokenize(code: str) -> set[str]:
    """Java-aware tokenization for Jaccard. Lowercase identifiers, drop keywords."""
    JAVA_KEYWORDS = {"public", "private", "class", "void", "return", "new", "if",
                     "else", "for", "while", "true", "false", "null", "this", "super",
                     "import", "package"}
    toks = JAVA_IDENTIFIER.findall(code.lower())
    return set(toks) - JAVA_KEYWORDS


def jaccard(a: str, b: str) -> float:
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union)


# ── Stage 1 + 2 (pure Python, no ML deps) ──────────────────────────────


def stage_1_path_filter(
    anchor: ReviewAnchor,
    archive_root: Path,
) -> list[dict]:
    """Find candidate review_comments in cross-crew PRs at the same path suffix.

    archive_root: paradigm-v2 state root containing repos/<repo>/archive/prs.sqlite3.
    """
    db = archive_root / "repos" / anchor.repo / "archive" / "prs.sqlite3"
    if not db.exists():
        return []
    suffix = anchor.path.split("/")[-1] if anchor.path else ""
    if not suffix:
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT rc.github_comment_id AS comment_id, rc.path, rc.line, rc.diff_hunk,
                   rc.user_login AS reviewer, rc.body, pr.number AS pr_number,
                   pr.author_login AS crew_login
            FROM pull_request_review_comments_current rc
            JOIN pull_requests_current pr ON pr.id = rc.pull_request_id
            WHERE rc.path LIKE ? AND pr.number != ?
              AND rc.in_reply_to_github_comment_id IS NULL
            LIMIT 500
            """,
            (f"%{suffix}", anchor.pr_number),  # exclude anchor's own PR (find cross-crew)
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def stage_2_jaccard_filter(
    anchor: ReviewAnchor,
    candidates: list[dict],
    threshold: float = JACCARD_THRESHOLD,
) -> list[tuple[dict, float]]:
    """Filter candidates by Jaccard similarity of diff_hunk vs anchor.code_hunk."""
    out: list[tuple[dict, float]] = []
    for c in candidates:
        hunk = c.get("diff_hunk") or ""
        j = jaccard(anchor.code_hunk, hunk)
        if j >= threshold:
            out.append((c, j))
    out.sort(key=lambda x: -x[1])
    return out


# ── Stage 3 (embedding, lazy ML) ────────────────────────────────────────


def stage_3_embed_rank(
    anchor: ReviewAnchor,
    jaccard_passed: list[tuple[dict, float]],
    encode_fn=None,
    top_k: int = STAGE3_TOP_K,
) -> list[tuple[dict, float, float]]:
    """Re-rank by embedding cosine. encode_fn override for tests."""
    if not jaccard_passed:
        return []
    if encode_fn is None:
        from rag.encoder import encode
        encode_fn = lambda texts: encode(texts, batch_size=8)
    pool = [c for c, _ in jaccard_passed]
    j_scores = [j for _, j in jaccard_passed]
    texts = [anchor.code_hunk] + [c.get("diff_hunk") or "" for c in pool]
    vecs = encode_fn(texts)
    anchor_vec = vecs[0]
    cosines = vecs[1:] @ anchor_vec
    norms = np.linalg.norm(vecs[1:], axis=1) * np.linalg.norm(anchor_vec)
    cosines = cosines / np.clip(norms, 1e-9, None)
    ranked = sorted(zip(pool, j_scores, cosines.tolist()), key=lambda x: -x[2])
    return ranked[:top_k]


# ── Stage 4 (AI veto, top-5 only, light prompt) ─────────────────────────


def stage_4_ai_veto_prompt(
    anchor: ReviewAnchor,
    candidates: list[tuple[dict, float, float]],
    top_n: int = STAGE4_TOP_K,
) -> str:
    """Build a small prompt; AI session decides which candidates pass veto.

    Output format expected from AI: one line per candidate
      `veto: <thread_id>=<keep|drop>`
    """
    parts = [
        "# F11 Stage 4 AI veto",
        f"Anchor (학습자 본인 thread `{anchor.thread_id}` at {anchor.path}:{anchor.line}):",
        f"```\n{anchor.code_hunk[:600]}\n```",
        f"Mentor ({anchor.mentor_login}): {anchor.comment_text[:400]}",
        "",
        "## Candidates (top-5 by embed_cosine):",
    ]
    for i, (c, j, cos) in enumerate(candidates[:top_n], start=1):
        parts.append(
            f"### Candidate #{i} `{anchor.repo}:{c.get('pr_number')}:{c.get('comment_id')}` "
            f"jaccard={j:.2f} cos={cos:.2f}"
        )
        parts.append(f"crew={c.get('crew_login')}, reviewer={c.get('reviewer')}")
        parts.append(f"path={c.get('path')}:{c.get('line')}")
        parts.append(f"```\n{(c.get('diff_hunk') or '')[:500]}\n```")
        parts.append(f"comment: {(c.get('body') or '')[:300]}")
    parts.append("\n## 판단 지침")
    parts.append("각 candidate가 anchor와 *같은 code 의도*에 대한 리뷰인가? 표면적 path/token 일치만이면 drop.")
    parts.append("출력: 한 줄씩 `veto: <repo:pr:comment_id>=keep|drop`")
    return "\n".join(parts)


def parse_ai_veto(text: str) -> dict[str, bool]:
    """Parse AI session output → {thread_id: kept_or_dropped}."""
    out: dict[str, bool] = {}
    for line in text.splitlines():
        m = re.match(r"\s*veto:\s*([\w:./-]+)\s*=\s*(keep|drop)", line.strip(), re.IGNORECASE)
        if m:
            out[m.group(1).strip()] = m.group(2).lower() == "keep"
    return out


def assemble_matches(
    anchor: ReviewAnchor,
    stage3_ranked: list[tuple[dict, float, float]],
    veto: dict[str, bool] | None = None,
) -> list[CrossCrewMatch]:
    """Combine stages → final CrossCrewMatch list (Stage 4 default kept if no veto)."""
    out: list[CrossCrewMatch] = []
    for c, j, cos in stage3_ranked:
        thread_id = f"{anchor.repo}:{c.get('pr_number')}:{c.get('comment_id')}"
        kept = veto.get(thread_id, True) if veto else True
        out.append(CrossCrewMatch(
            anchor_thread_id=anchor.thread_id,
            candidate_repo=anchor.repo,
            candidate_pr=c.get("pr_number", 0),
            candidate_path=c.get("path") or "",
            candidate_line=c.get("line"),
            candidate_diff_hunk=(c.get("diff_hunk") or "")[:600],
            candidate_reviewer=c.get("reviewer") or "",
            candidate_comment=(c.get("body") or "")[:600],
            crew_login=c.get("crew_login") or "",
            jaccard=j,
            embed_cosine=cos,
            ai_veto=kept,
            stage_passed=4 if veto else 3,
        ))
    return [m for m in out if m.ai_veto]
