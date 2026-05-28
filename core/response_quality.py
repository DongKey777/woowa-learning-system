"""Phase T4: learn-response-quality — telemetry for AI session answer quality.

AI session auto-calls after every coach turn. Records:
- source event id (the rag_ask / coach_run this answer responds to)
- response summary + exact final-answer redacted excerpt (≤5000 chars)
- optional redacted full-body file for path/stdin/text captures
- expected citations (from coach prompt's response_hints.citation_paths)
- declared citations (from actual final answer's 참고: block)
- quality/contract flags (missing_body, citation_mismatch, possible_summary_body, …)
PII redaction: emails / Bearer tokens / API keys / phone numbers replaced
with placeholder before excerpt/full-body sidecar storage.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from core.state import DEFAULT_STATE_ROOT, append_history_event

SCHEMA_ID = "assistant-response-quality-v1"
EXCERPT_MAX_CHARS = 5000
QUALITY_LOG = "response-quality.jsonl"
BODY_DIR = "response-bodies"
BODY_STORAGE_SCHEME = "sha256-redacted-v1"

# PII patterns
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_BEARER_RE = re.compile(r"\b(Bearer|Token|Authorization)[: ]+[A-Za-z0-9._\-]{12,}\b",
                          re.IGNORECASE)
_API_KEY_RE = re.compile(r"\b(sk|pk|api_key|apikey|gho|ghs|ghp|ghu|github_pat|"
                          r"AKIA|AIza)[\-_]?[A-Za-z0-9_]{16,}\b")
_PHONE_RE = re.compile(
    r"(?<![\w@])"
    r"\+?\d{1,3}(?:[- ]?\d{1,4}){2,3}[- ]?\d{4}"
    r"(?![\w@])"
)


def redact_pii(text: str) -> str:
    """Replace PII patterns with placeholders. Preserves length-ish for readability."""
    if not text:
        return text
    out = _EMAIL_RE.sub("[EMAIL]", text)
    out = _BEARER_RE.sub("[BEARER_TOKEN]", out)
    out = _API_KEY_RE.sub("[API_KEY]", out)
    out = _PHONE_RE.sub("[PHONE]", out)
    return out


def _store_body_sidecar(state_root: Path, redacted_body: str) -> tuple[str, str, int, bool]:
    body_hash = hashlib.sha256(redacted_body.encode("utf-8")).hexdigest()
    body_path = (state_root / "learner" / BODY_DIR / "sha256"
                 / body_hash[:2] / f"{body_hash}.md")
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_bytes = len(redacted_body.encode("utf-8"))
    try:
        with body_path.open("x", encoding="utf-8") as f:
            f.write(redacted_body)
        deduped = False
    except FileExistsError:
        deduped = True
        body_bytes = 0
    return str(body_path.relative_to(state_root)), body_hash, body_bytes, deduped


def detect_citation_drift(
    expected: list[str] | None,
    declared: list[str] | None,
) -> set[str]:
    """Compare expected vs declared citation paths.

    Returns set of drift flags:
      - "missing_citation": declared empty but expected non-empty
      - "extra_citation": declared has paths not in expected
      - "citation_mismatch": declared paths differ from expected (any subset miss)
    """
    flags: set[str] = set()
    exp = set(expected or [])
    dec = set(declared or [])
    if exp and not dec:
        flags.add("missing_citation")
    elif dec - exp:
        flags.add("extra_citation")
    elif exp - dec:
        flags.add("citation_mismatch")
    return flags


def record_response_quality(
    source_event_id: str,
    response_summary: str,
    response_body: str | None = None,
    *,
    expected_citation_paths: list[str] | None = None,
    declared_citation_paths: list[str] | None = None,
    quality_flags: list[str] | None = None,
    contract_flags: list[str] | None = None,
    repo: str | None = None,
    turn_id: str | None = None,
    learner_id: str = "default",
    state_root: Path = DEFAULT_STATE_ROOT,
    now: float | None = None,
    append_event: bool = True,
    mode: str | None = None,
    capture_method: str | None = None,
    capture_input_chars: int | None = None,
) -> dict:
    """Build telemetry row, write to response-quality.jsonl, optionally append
    a `response_quality` event to history.jsonl for join with other events.

    Returns the written row (dict).
    """
    ts = now or time.time()
    event_mode = mode or os.environ.get("WOOWA_SESSION_MODE", "learning")
    flags = set(quality_flags or [])
    cflags = list(contract_flags or [])

    # Auto-detect: missing body
    body = response_body or ""
    if not body.strip():
        flags.add("missing_response_body")

    # Auto-detect: citation drift
    flags |= detect_citation_drift(expected_citation_paths, declared_citation_paths)

    redacted_body = redact_pii(body) if body else ""
    excerpt = redacted_body[:EXCERPT_MAX_CHARS] if redacted_body else ""
    response_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16] if body else ""
    response_body_path = None
    response_body_bytes = len(body.encode("utf-8")) if body else 0
    response_body_hash = ""
    response_body_stored_bytes = 0
    response_body_deduped = False
    if body:
        (
            response_body_path,
            response_body_hash,
            response_body_stored_bytes,
            response_body_deduped,
        ) = _store_body_sidecar(state_root, redacted_body)

    row = {
        "schema_id": SCHEMA_ID,
        "logged_at": ts,
        "source_event_id": source_event_id,
        "turn_id": turn_id or f"turn-{uuid.uuid4().hex[:12]}",
        "repo": repo,
        "mode": event_mode,
        "learner_id": learner_id,
        "response_summary": (response_summary or "")[:300],
        "response_excerpt": excerpt,
        "response_length_chars": len(body),
        "response_body_bytes": response_body_bytes,
        "response_body_path": response_body_path,
        "response_body_storage": BODY_STORAGE_SCHEME if body else None,
        "response_body_hash": response_body_hash,
        "response_body_stored_bytes": response_body_stored_bytes,
        "response_body_deduped": response_body_deduped,
        "response_excerpt_truncated": len(redacted_body) > EXCERPT_MAX_CHARS,
        "response_hash": response_hash,
        "capture_method": capture_method or ("unknown_body" if body else "none"),
        "capture_input_chars": capture_input_chars,
        "citation_paths_expected": list(expected_citation_paths or []),
        "citation_paths_declared": list(declared_citation_paths or []),
        "quality_flags": sorted(flags),
        "contract_flags": cflags,
    }

    out = state_root / "learner" / QUALITY_LOG
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    if append_event:
        # Also surface as a learner history event so mining tools can join
        append_history_event({
            "event_id": f"response_quality-{int(ts * 1000)}-{uuid.uuid4().hex[:6]}",
            "event_type": "response_quality",
            "mode": event_mode,
            "ts": ts,
            "learner_id": learner_id,
            "repo": repo,
            "payload": {
                "source_event_id": source_event_id,
                "quality_flags": sorted(flags),
                "summary": row["response_summary"],
                "capture_method": row["capture_method"],
                "response_body_path": response_body_path,
                "response_length_chars": len(body),
            },
        }, state_root=state_root)

    return row
