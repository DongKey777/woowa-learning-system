"""mission.predict — family K (pre-push prediction synthesis, mode predict).

Repo-scoped, learner-scoped. Answers "다음 PR 올리면 뭘 지적받을까?": it does
NOT re-query SQLite or git — it SYNTHESIZES three already-built per-repo
artifacts into a forward-looking, advisory prediction. Pure read-only fusion;
every output field traces back to real input data (no fabricated numbers).

Inputs read under state/repos/<repo>/:
  reviewer_profile.json (C) — the mentor's recurring review topics, hotspot
                              files, and review-state mix. The "what gets
                              flagged" signal.
  cohort.json           (F) — the learner's PR placed against the cohort
                              (percentile, review rounds). The "review load"
                              signal.
  pr_diff_evolution.json (H) — per-PR hotspots + detected smells from the most
                              recent PR. The "what's in your code now" signal.

Each input may be absent or carry a non-"ok" status → that input is treated as
unavailable and its derived section becomes empty; the build never crashes.
Status is "missing_inputs" only when all three inputs are unavailable.
Artifact lands at state/repos/<repo>/predict.json.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from core.state import DEFAULT_STATE_ROOT

_TOP_TOPICS = 8
_TOP_HOTSPOTS = 10
_TOP_SMELLS = 10


def _load_artifact(path: Path) -> dict | None:
    """Read a JSON artifact defensively; missing/invalid → None."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _is_ok(artifact: dict | None) -> bool:
    return artifact is not None and artifact.get("status") == "ok"


def _likely_review_topics(reviewer: dict | None) -> list[dict]:
    """Top recurring topics from C → {topic, mentions}."""
    if not _is_ok(reviewer):
        return []
    topics = reviewer.get("recurring_topics") or []
    out: list[dict] = []
    for entry in topics[:_TOP_TOPICS]:
        if not isinstance(entry, dict):
            continue
        out.append({"topic": entry.get("topic"),
                    "mentions": entry.get("n")})
    return out


def _recent_pr(evolution: dict | None) -> dict | None:
    """Most recent PR from H = the one with the max number."""
    if not _is_ok(evolution):
        return None
    prs = evolution.get("prs") or []
    candidates = [p for p in prs if isinstance(p, dict)]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.get("number") or 0)


def _likely_hotspot_files(reviewer: dict | None, recent_pr: dict | None
                          ) -> list[dict]:
    """Merge C hotspot_files (bare filename) with H most-recent-PR hotspots
    (basename of path), keyed by basename. review_comments = max of the two
    counts; sources lists which inputs contributed."""
    merged: dict[str, dict] = {}

    if _is_ok(reviewer):
        for entry in reviewer.get("hotspot_files") or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("file")
            if not name:
                continue
            base = os.path.basename(str(name))
            n = entry.get("n") or 0
            bucket = merged.setdefault(
                base, {"file": base, "review_comments": 0, "sources": []})
            bucket["review_comments"] = max(bucket["review_comments"], n)
            if "reviewer_profile" not in bucket["sources"]:
                bucket["sources"].append("reviewer_profile")

    if recent_pr is not None:
        for entry in recent_pr.get("hotspots") or []:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if not path:
                continue
            base = os.path.basename(str(path))
            comments = entry.get("comments") or 0
            bucket = merged.setdefault(
                base, {"file": base, "review_comments": 0, "sources": []})
            bucket["review_comments"] = max(bucket["review_comments"], comments)
            if "pr_diff_evolution" not in bucket["sources"]:
                bucket["sources"].append("pr_diff_evolution")

    files = sorted(merged.values(),
                   key=lambda b: b["review_comments"], reverse=True)
    return files[:_TOP_HOTSPOTS]


def _smell_warnings(recent_pr: dict | None) -> list[dict]:
    """Top smells from H most-recent PR → {concept_id, value, file, line}."""
    if recent_pr is None:
        return []
    out: list[dict] = []
    for entry in (recent_pr.get("smells") or [])[:_TOP_SMELLS]:
        if not isinstance(entry, dict):
            continue
        out.append({
            "concept_id": entry.get("concept_id"),
            "value": entry.get("value"),
            "file": entry.get("path"),
            "line": entry.get("line"),
        })
    return out


def _comparison_percentile(cohort: dict | None, metric: str) -> int | None:
    if not _is_ok(cohort):
        return None
    for row in cohort.get("comparison") or []:
        if isinstance(row, dict) and row.get("metric") == metric:
            return row.get("percentile")
    return None


def _learner_recent_review_rounds(cohort: dict | None) -> int | None:
    if not _is_ok(cohort):
        return None
    prs = cohort.get("learner_prs") or []
    if not prs or not isinstance(prs[0], dict):
        return None
    return prs[0].get("review_rounds")


def _cohort_median_review_rounds(cohort: dict | None) -> int | None:
    if not _is_ok(cohort):
        return None
    block = cohort.get("cohort")
    if not isinstance(block, dict):
        return None
    return block.get("median_review_rounds")


def _mentor_review_state(reviewer: dict | None, state: str) -> int | None:
    if not _is_ok(reviewer):
        return None
    states = reviewer.get("review_states")
    if not isinstance(states, dict):
        return None
    return states.get(state)


def _projection_sentence(pct: int | None, rounds: int | None,
                         median_rounds: int | None,
                         changes_requested: int | None,
                         approved: int | None) -> str:
    """One natural Korean sentence assembled ONLY from non-null numbers."""
    parts: list[str] = []
    if pct is not None:
        # percentile = 나보다 작거나 같은 동료 비율(coach.py 정의) — 낮을수록 작은 PR.
        # 기존 '상위 N percentile'은 낮은 pct를 '큰 PR'로 뒤집어 읽혔다.
        parts.append(f"이번 PR 규모는 동기 {pct}%보다 큰 편이라")
    if rounds is not None and median_rounds is not None:
        parts.append(
            f"지난 PR 리뷰가 {rounds}라운드였고 동기 평균은 {median_rounds}라운드라 "
            f"이번에도 비슷한 정도의 리뷰가 오갈 것 같아")
    elif rounds is not None:
        parts.append(f"지난 PR은 리뷰가 {rounds}라운드 돌았으니 이번에도 한두 차례 더 주고받을 수 있어")
    elif median_rounds is not None:
        parts.append(f"동기들은 보통 {median_rounds}라운드쯤 리뷰를 주고받으니 그 정도는 예상해 두자")
    if changes_requested is not None and approved is not None:
        parts.append(
            f"멘토가 지금까지 변경 요청 {changes_requested}번, 승인 {approved}번을 줬으니 "
            f"꼼꼼히 보는 편이야")
    elif changes_requested is not None:
        parts.append(f"멘토가 변경 요청을 {changes_requested}번 줬을 만큼 디테일을 챙기는 편이야")
    elif approved is not None:
        parts.append(f"멘토가 승인을 {approved}번 준 이력이 있어")

    if not parts:
        return "예측에 쓸 과거 데이터가 아직 부족해."
    return ", ".join(parts).rstrip(", ") + "."


def analyze_repo(repo: str, learner_login: str = "DongKey777",
                 state_root: Path = DEFAULT_STATE_ROOT,
                 now: float | None = None) -> dict:
    """Synthesize the pre-push prediction artifact for one repo. Read-only;
    fuses reviewer_profile + cohort + pr_diff_evolution. Never crashes."""
    if now is None:
        generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    else:
        generated_at = dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()

    repo_dir = state_root / "repos" / repo
    reviewer = _load_artifact(repo_dir / "reviewer_profile.json")
    cohort = _load_artifact(repo_dir / "cohort.json")
    evolution = _load_artifact(repo_dir / "pr_diff_evolution.json")

    inputs = {
        "reviewer_profile": _is_ok(reviewer),
        "cohort": _is_ok(cohort),
        "pr_diff_evolution": _is_ok(evolution),
    }
    status = "ok" if any(inputs.values()) else "missing_inputs"

    recent_pr = _recent_pr(evolution)

    likely_review_topics = _likely_review_topics(reviewer)
    likely_hotspot_files = _likely_hotspot_files(reviewer, recent_pr)
    smell_warnings = _smell_warnings(recent_pr)

    pr_size_percentile = _comparison_percentile(cohort, "total_changes")
    learner_review_rounds = _learner_recent_review_rounds(cohort)
    cohort_median_review_rounds = _cohort_median_review_rounds(cohort)
    mentor_changes_requested = _mentor_review_state(reviewer, "CHANGES_REQUESTED")
    mentor_approved = _mentor_review_state(reviewer, "APPROVED")

    review_load_projection = {
        "pr_size_percentile": pr_size_percentile,
        "learner_review_rounds": learner_review_rounds,
        "cohort_median_review_rounds": cohort_median_review_rounds,
        "mentor_changes_requested": mentor_changes_requested,
        "mentor_approved": mentor_approved,
        "projection": _projection_sentence(
            pr_size_percentile, learner_review_rounds,
            cohort_median_review_rounds, mentor_changes_requested,
            mentor_approved),
    }

    return {
        "repo": repo,
        "status": status,
        "learner_login": learner_login,
        "inputs": inputs,
        "likely_review_topics": likely_review_topics,
        "likely_hotspot_files": likely_hotspot_files,
        "smell_warnings": smell_warnings,
        "review_load_projection": review_load_projection,
        "counts": {
            "likely_topics": len(likely_review_topics),
            "likely_hotspots": len(likely_hotspot_files),
            "smells": len(smell_warnings),
        },
        "generated_at": generated_at,
    }


def save_predict(report: dict, state_root: Path = DEFAULT_STATE_ROOT) -> Path:
    out = state_root / "repos" / report["repo"] / "predict.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out
