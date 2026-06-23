"""Peer PR diff comparison + Korean nickname → author_login resolver.

Replaces legacy `scripts/workbench/core/peer_pr_precise.py` (913 LOC) with
SQLite query + simple stitcher. AI session does the comparison reasoning
in Phase 6.5; this module just gathers the evidence.

Nickname resolution: PR titles in Woowa missions follow patterns like
`[1단계 - 콘솔] 동키` / `[2단계] 큐빈` → mine author_login ↔ Korean nickname
from archive titles. Result cached as dict in memory per call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.archive import (
    DEFAULT_STATE_ROOT,
    get_diff_files,
    get_pr,
    get_review_threads,
)

NICKNAME_PATTERNS = (
    re.compile(r"\[[^\]]*\]\s*([가-힣]{2,6})\s*$"),     # [..stage..] 닉네임
    re.compile(r"^([가-힣]{2,6})\s+미션"),               # 닉네임 미션
    re.compile(r"\s([가-힣]{2,6})\s*$"),                 # trailing Korean word
)


@dataclass
class PeerPRComparison:
    repo: str
    learner_pr: dict
    peer_prs: list[dict] = field(default_factory=list)
    common_paths: list[str] = field(default_factory=list)
    learner_only_paths: list[str] = field(default_factory=list)
    peer_only_paths: list[str] = field(default_factory=list)
    representative_threads: dict[int, list[dict]] = field(default_factory=dict)


def extract_nickname(title: str) -> str | None:
    if not title:
        return None
    for pat in NICKNAME_PATTERNS:
        m = pat.search(title)
        if m:
            return m.group(1)
    return None


def compare(
    repo: str,
    learner_pr_number: int,
    peer_pr_numbers: list[int],
    max_peers: int = 2,
    state_root=DEFAULT_STATE_ROOT,
) -> PeerPRComparison:
    """Gather learner + peer PR diffs/threads. Hard cap max_peers (learner safety)."""
    if len(peer_pr_numbers) > max_peers:
        peer_pr_numbers = peer_pr_numbers[:max_peers]

    learner_pr = get_pr(repo, learner_pr_number, state_root=state_root)
    if learner_pr is None:
        raise ValueError(f"learner PR #{learner_pr_number} not in archive")

    learner_files = get_diff_files(repo, learner_pr_number, state_root=state_root)
    learner_paths = {f["path"] for f in learner_files}

    peer_pr_records: list[dict] = []
    peer_paths_union: set[str] = set()
    threads_by_pr: dict[int, list[dict]] = {}
    for pn in peer_pr_numbers:
        pr = get_pr(repo, pn, state_root=state_root)
        if pr is None:
            continue
        files = get_diff_files(repo, pn, state_root=state_root)
        pr["files"] = files
        pr["nickname"] = extract_nickname(pr.get("title", ""))
        peer_pr_records.append(pr)
        peer_paths_union.update(f["path"] for f in files)
        threads_by_pr[pn] = get_review_threads(repo, pn, state_root=state_root)

    threads_by_pr[learner_pr_number] = get_review_threads(
        repo, learner_pr_number, state_root=state_root
    )

    common = sorted(learner_paths & peer_paths_union)
    learner_only = sorted(learner_paths - peer_paths_union)
    peer_only = sorted(peer_paths_union - learner_paths)

    return PeerPRComparison(
        repo=repo,
        learner_pr={**learner_pr, "files": learner_files,
                    "nickname": extract_nickname(learner_pr.get("title", ""))},
        peer_prs=peer_pr_records,
        common_paths=common,
        learner_only_paths=learner_only,
        peer_only_paths=peer_only,
        representative_threads=threads_by_pr,
    )
