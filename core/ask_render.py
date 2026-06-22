"""core/ask_render.py — shared slimming for session-facing ask stdout.

Two code paths print response_hints / response_quality_hint to stdout for the AI
session to read: the warm daemon path (daemon._render_ask_stdout, action=ask_text
— what the session actually receives) and the cold Python client (bin/ask). The
session only pastes citation_markdown and branches on a few gate fields; the
parallel citation arrays (citation_paths / citation_concept_ids / citation_trace)
and the full quality hint are telemetry that stays in --json and the Stop-hook
transcript. Keeping the key allow-lists in ONE place stops the two render paths
from drifting — the warm path silently dumped the full ~3300-char telemetry block
for one release because the slim lived only in the Python client.
"""

# The session reads these; everything else in response_hints is telemetry (--json).
SESSION_HINT_KEYS = (
    "citation_markdown",
    "rerank_gate",
    "dense_margin",
    "reformulated_query",
    "tier_downgrade",
    "fallback_disclaimer",
)
# No-hook fallback essentials; the Stop hook reads the full hint from the transcript.
SESSION_QUALITY_KEYS = ("command_template", "source_event_id")


def session_response_hints(hints):
    """Return the session-facing subset of response_hints (or None if empty)."""
    if not hints:
        return None
    slim = {k: hints[k] for k in SESSION_HINT_KEYS if hints.get(k) is not None}
    return slim or None


def session_quality_hint(rq_hint):
    """Return the no-hook fallback subset of response_quality_hint (or None)."""
    if not rq_hint:
        return None
    slim = {k: rq_hint[k] for k in SESSION_QUALITY_KEYS if rq_hint.get(k) is not None}
    return slim or None
