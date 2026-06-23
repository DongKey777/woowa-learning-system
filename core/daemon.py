"""BGE-M3 keep-alive daemon over AF_UNIX line-delimited JSON."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_ROOT = Path(__file__).resolve().parent.parent / "state"
DAEMON_TIMEOUT = 30  # seconds per request
PID_FILE = "rag-daemon.pid"
SOCKET_FILE = "rag-daemon.sock"
LISTEN_BACKLOG = 32
DEFAULT_LOG_PATH = Path("/tmp/daemon.log")
DEFAULT_ENCODER_WARM_QUERIES = ("DI",)


# ── client API ────────────────────────────────────────────────────────────


def _socket_path(state_root: Path) -> Path:
    return state_root / SOCKET_FILE


def _pid_path(state_root: Path) -> Path:
    return state_root / PID_FILE


def search(
    query: str,
    top_k: int = 5,
    relations_expand: int = 3,
    state_root: Path = DEFAULT_STATE_ROOT,
    learner_id: str | None = None,
    lexical_expand: int | None = None,
) -> list[dict] | None:
    """Returns hits via daemon. None if daemon unavailable (caller falls back).

    lexical_expand=0 disables the fusion rerank for a pure dense top-k (the
    signal the semantic router gate consumes); None keeps the server default."""
    sp = _socket_path(state_root)
    if not sp.exists():
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(DAEMON_TIMEOUT)
        sock.connect(str(sp))
        req_payload = {
            "action": "search",
            "query": query,
            "top_k": top_k,
            "relations_expand": relations_expand,
        }
        if lexical_expand is not None:
            req_payload["lexical_expand"] = lexical_expand
        if learner_id:
            req_payload["learner_id"] = learner_id
        req = json.dumps(req_payload) + "\n"
        sock.sendall(req.encode("utf-8"))
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        sock.close()
        resp = json.loads(data.decode("utf-8").strip())
        return resp.get("hits", [])
    except (OSError, json.JSONDecodeError):
        return None

def ask(
    prompt: str,
    repo: str | None = None,
    learner_id: str = "default",
    state_root: Path = DEFAULT_STATE_ROOT,
) -> dict | None:
    """Full ask pipeline via daemon. Returns None if daemon unavailable.

    Reads until socket close (server closes after sendall) — JSON contains
    raw \\n in escaped form, so newline-delimited not safe for large payloads.
    """
    sp = _socket_path(state_root)
    if not sp.exists():
        return None
    if learner_id == "default":
        from core.identity import resolve_learner_login
        learner_id = resolve_learner_login(state_root)
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(DAEMON_TIMEOUT)
        sock.connect(str(sp))
        req = json.dumps({
            "action": "ask",
            "query": prompt,
            "repo": repo,
            "learner_id": learner_id,
            "mode": os.environ.get("WOOWA_SESSION_MODE", "learning"),
            "reformulation": os.environ.get("WOOWA_REFORMULATE"),
        }) + "\n"
        sock.sendall(req.encode("utf-8"))
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        sock.close()
        return json.loads(data.decode("utf-8").strip())
    except (OSError, json.JSONDecodeError):
        return None

def _render_ask_stdout(resp: dict, json_route: bool = False) -> str:
    # Slim to the session-facing subset (shared with bin/ask via core.ask_render)
    # — the parallel citation arrays + full quality hint are telemetry and stay
    # in --json / the Stop-hook transcript, not in what the session re-reads.
    from core.ask_render import session_quality_hint, session_response_hints

    lines: list[str] = []
    if json_route:
        lines.append("# RouteDecision: " + json.dumps({
            "mode": resp.get("mode"),
            "personas": resp.get("personas", []),
            "budget_tokens": resp.get("budget"),
            "reason": resp.get("reason"),
        }, ensure_ascii=False))
    hints = session_response_hints(resp.get("response_hints"))
    if hints:
        lines.append("# response_hints: " + json.dumps(hints, ensure_ascii=False))
    rq_hint = session_quality_hint(resp.get("response_quality_hint"))
    if rq_hint:
        lines.append("# response_quality_hint: " + json.dumps(rq_hint, ensure_ascii=False))
    lines.append(resp["markdown"])
    return "\n".join(lines) + "\n"


def _is_capturable_learning_turn(event_mode: str | None, raw_utterance) -> bool:
    """A pending capture is created only for a genuine learning turn — one in
    learning mode that carries a real learner utterance (--raw-utterance, the
    CLAUDE.md §4.2 contract marker). Bare bin/ask probes and multi-intent
    sub-asks (no raw_utterance) are excluded so the Stop hook's latest-pending
    attach can't silently absorb the session's next unrelated message into them.
    """
    return event_mode == "learning" and bool(raw_utterance)


def ping(state_root: Path = DEFAULT_STATE_ROOT) -> bool:
    sp = _socket_path(state_root)
    if not sp.exists():
        return False
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect(str(sp))
        sock.sendall(b'{"action":"ping"}\n')
        data = sock.recv(1024)
        sock.close()
        return json.loads(data.decode("utf-8")).get("alive") is True
    except (OSError, json.JSONDecodeError):
        return False


def stop(state_root: Path = DEFAULT_STATE_ROOT) -> bool:
    pid_p = _pid_path(state_root)
    sp = _socket_path(state_root)
    if not pid_p.exists():
        return False
    pid = int(pid_p.read_text())
    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        pass
    pid_p.unlink(missing_ok=True)
    sp.unlink(missing_ok=True)
    return True


def _encoder_warm_queries() -> tuple[str, ...]:
    raw = os.environ.get("WOOWA_DAEMON_ENCODER_WARM_QUERIES")
    if raw is None:
        return DEFAULT_ENCODER_WARM_QUERIES
    queries = tuple(q.strip() for q in raw.split("|") if q.strip())
    return queries or DEFAULT_ENCODER_WARM_QUERIES


def _prewarm_encoder_queries() -> tuple[str, ...]:
    from rag.encoder import encode_query

    warmed: list[str] = []
    for query in _encoder_warm_queries():
        encode_query(query)
        warmed.append(query)
    return tuple(warmed)


def _encoder_import_prime_enabled() -> bool:
    raw = os.environ.get("WOOWA_DAEMON_ENCODER_IMPORT_PRIME", "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _prime_parallel_startup_imports() -> None:
    """Import lightweight modules before concurrent BGE/corpus warm-up.

    Python 3.13 can expose partially initialized stdlib modules when startup
    threads import typing/dataclasses at the same time. Priming these imports
    keeps daemon readiness deterministic without moving heavy model work back
    onto the critical path.
    """
    import dataclasses
    import typing

    from rag.corpus_loader import LoadedCorpus

    _ = (dataclasses.dataclass, typing.ClassVar, LoadedCorpus)


def start_background(
    state_root: Path = DEFAULT_STATE_ROOT,
    log_path: Path = DEFAULT_LOG_PATH,
    timeout_s: float = 90.0,
) -> bool:
    """Start daemon in a detached process and wait until ping succeeds."""
    if ping(state_root):
        return True
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "bin" / "rag-daemon"),
        "start",
        "--state-root",
        str(state_root),
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
    )
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if ping(state_root):
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.05)
    return False


# ── server (daemon) ───────────────────────────────────────────────────────


def _safe_send(conn, data: bytes) -> bool:
    """Send on a client connection, swallowing a disconnect. Returns False if the
    client is gone. A BrokenPipe/OSError here must NEVER propagate: the error-path
    send sits inside the per-connection ``except`` block, so a raise there unwinds
    past the request loop into the outer ``finally`` that closes + unlinks the
    daemon socket and pid — i.e. one client that disconnects before reading the
    response (Ctrl-C, timeout) would tear the whole daemon down."""
    try:
        conn.sendall(data)
        return True
    except OSError:
        return False


def serve(state_root: Path = DEFAULT_STATE_ROOT) -> None:
    """Daemon main loop. Loads BGE-M3 once, serves search + ask requests forever."""
    sp = _socket_path(state_root)
    pid_p = _pid_path(state_root)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.unlink(missing_ok=True)

    pid_p.write_text(str(os.getpid()))
    startup_t0 = time.perf_counter()
    startup_timings: dict[str, float | int | list[str]] = {"pid": os.getpid()}

    def _timed_startup(name: str, fn):
        t0 = time.perf_counter()
        try:
            return fn()
        finally:
            startup_timings[f"{name}_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    # Pre-load encoder + corpus + ask pipeline.
    def _import_rag_search():
        from rag.search import _cached_open_index, search as loaded_search

        _cached_open_index()
        return loaded_search

    def _load_runtime_corpus():
        from rag.corpus_loader import load_corpus_runtime

        loaded_corpus = load_corpus_runtime()
        # These caches are needed by the first real dense query. Loading them
        # while BGE-M3 materializes keeps learner-facing first ask under the
        # warm-path budget without delaying readiness on the critical path.
        try:
            from core.lexical_fusion import preload_lexical_entries
            from rag.search import _exact_shortcut_lookup

            preload_lexical_entries(loaded_corpus, build_if_missing=False)
            _exact_shortcut_lookup(loaded_corpus)
        except Exception:  # noqa: BLE001
            pass
        return loaded_corpus

    _prime_parallel_startup_imports()
    if _encoder_import_prime_enabled():
        from rag.encoder import prime_encoder_backend_imports

        startup_timings["encoder_backend"] = _timed_startup(
            "encoder_imports",
            prime_encoder_backend_imports,
        )
    else:
        startup_timings["encoder_backend"] = "deferred"
        startup_timings["encoder_imports_ms"] = 0.0
    with ThreadPoolExecutor(max_workers=3) as pool:
        search_future = pool.submit(
            lambda: _timed_startup("search_import_open_index", _import_rag_search)
        )
        corpus_future = pool.submit(
            lambda: _timed_startup("corpus_cache_prewarm", _load_runtime_corpus)
        )
        encoder_future = pool.submit(
            lambda: _timed_startup("encoder_prewarm", _prewarm_encoder_queries)
        )

        rag_search = search_future.result()
        from core.coach import compose
        from core.feedback import record_turn
        from core.identity import resolve_learner_login
        from core.lexical_fusion import make_lexical_fusion_fn
        from core.lazy_loader import (
            _hits_to_dicts,
            _personalize_hits,
            load as lazy_load,
        )
        from core.reformulate import reformulation_enabled, select_reformulation
        from core.router import route, strip_override_tokens
        from core.state import append_history_event, load_profile, read_history
        from core.trigger import (
            load_drill_history,
            load_pending_triggers,
            load_profile_payload,
            select_cognitive_trigger,
            write_pending_triggers_atomic,
        )
        corpus = corpus_future.result()
        startup_timings["encoder_warm_queries"] = list(encoder_future.result())
    try:
        from rag.index import check_corpus_drift

        drift_warning = check_corpus_drift(corpus)
        if drift_warning:
            print(f"[daemon] WARNING {drift_warning}", file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001
        pass  # drift check is advisory — never block startup
    lexical_fns: dict[int, object] = {}

    def _lexical_fn(loaded_corpus, expand: int):
        if expand <= 0:
            return None
        if expand not in lexical_fns:
            lexical_fns[expand] = make_lexical_fusion_fn(loaded_corpus, expand=expand)
        return lexical_fns[expand]

    def _nearest_concept_cosine(text: str) -> float | None:
        """Top-1 dense cosine for the semantic router rescue (Pillar 2).

        Only invoked by route() when the lexical CS_TOKENS guard would drop a
        cs_qa prompt and WOOWA_SEMANTIC_ROUTER_THRESHOLD is set, so it adds zero
        cost on the happy path. Uses the warm encoder + Lance index already held
        by the daemon."""
        try:
            hits = rag_search(text, top_k=1, relations_expand=0, rerank_fn=None, corpus=corpus)
        except Exception:  # noqa: BLE001
            return None
        return float(hits[0].score) if hits else None

    startup_timings["total_to_ready_ms"] = round(
        (time.perf_counter() - startup_t0) * 1000,
        1,
    )
    print(
        "[daemon] startup_timings "
        + json.dumps(startup_timings, ensure_ascii=False, sort_keys=True),
        flush=True,
    )
    print(f"[daemon] ready (pid={os.getpid()}, socket={sp})", flush=True)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sp))
    # bin/ask clients may arrive in small bursts from AI sessions or tests.
    # A larger backlog keeps those clients queued instead of forcing a cold
    # in-process fallback that lacks daemon-only telemetry hints.
    srv.listen(LISTEN_BACKLOG)
    try:
        while True:
            conn, _ = srv.accept()
            try:
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(8192)
                    if not chunk:
                        break
                    data += chunk
                if not data:
                    conn.close()
                    continue
                req = json.loads(data.decode("utf-8").strip())
                action = req.get("action")
                if action == "ping":
                    conn.sendall(json.dumps({"alive": True, "ts": time.time()}).encode() + b"\n")
                elif action == "search":
                    hits = rag_search(
                        req["query"],
                        top_k=req.get("top_k", 5),
                        relations_expand=req.get("relations_expand", 3),
                        rerank_fn=_lexical_fn(corpus, req.get("lexical_expand", 8)),
                        corpus=corpus,
                    )
                    personalization = None
                    if req.get("learner_id"):
                        profile = load_profile(req["learner_id"], state_root=state_root)
                        hits, personalization = _personalize_hits(hits, profile)
                    payload = {"hits": _hits_to_dicts(hits)}
                    if personalization is not None:
                        payload["personalization"] = personalization
                    conn.sendall(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
                elif action in ("ask", "ask_text"):
                    ask_started = time.perf_counter()
                    prompt = req["query"]
                    repo = req.get("repo")
                    learner_id = req.get("learner_id", "default")
                    if learner_id == "default":
                        learner_id = resolve_learner_login(state_root)
                    event_mode = req.get("mode") or os.environ.get("WOOWA_SESSION_MODE", "learning")
                    # Provenance of event_mode: resolved by bin/ask (explicit/env/
                    # default). If a caller predates mode_source, fall back to what
                    # the daemon can observe — env presence vs. conservative default.
                    event_mode_source = req.get("mode_source") or (
                        "env" if os.environ.get("WOOWA_SESSION_MODE") else "default"
                    )
                    route_mode = req.get("route_mode")  # AI-session-driven mode (optional)
                    profile = load_profile(learner_id, state_root=state_root)
                    recent = read_history(state_root=state_root, tail=20)
                    reformulation = select_reformulation(
                        prompt=prompt,
                        explicit_reformulated_query=req.get("reformulated_query"),
                        history=recent,
                        corpus=corpus,
                        repo=repo,
                        enabled=reformulation_enabled(req.get("reformulation")),
                    )
                    reformulated_query = reformulation.reformulated_query if reformulation else ""
                    retrieval_query = reformulated_query or strip_override_tokens(prompt)
                    decision = route(
                        prompt, repo=repo,
                        pending_self_assessment=profile.pending_triggers.get("self_assessment"),
                        pending_drill=profile.pending_triggers.get("review_drill"),
                        nearest_concept_cosine=_nearest_concept_cosine,
                        mode_override=route_mode,
                    )
                    if decision.mode == "tier_0_fallback" and reformulated_query:
                        decision = route(
                            reformulated_query, repo=repo,
                            pending_self_assessment=profile.pending_triggers.get("self_assessment"),
                            pending_drill=profile.pending_triggers.get("review_drill"),
                            nearest_concept_cosine=_nearest_concept_cosine,
                            mode_override=route_mode,
                        )
                    artifacts = lazy_load(
                        decision,
                        repo=repo,
                        state_root=state_root,
                        query=retrieval_query,
                        corpus=corpus,
                        learner_profile=profile,
                    )
                    # Drill mode integration: if router dispatched drill and
                    # there's no pending offer yet, try to build one from the
                    # learner's uncertain concepts so the AI session has a
                    # concrete question to present.
                    if decision.mode == "drill":
                        from core.drill import build_offer_if_due
                        uncertain = profile.uncertain_concepts or []
                        offer = build_offer_if_due(
                            learner_id=learner_id, state_root=state_root,
                            uncertain_concept_ids=uncertain,
                        )
                        if offer:
                            artifacts["drill_offer"] = {
                                "concept_id": offer.concept_id,
                                "question": offer.question,
                                "expected_terms": offer.expected_terms,
                                "source": offer.source,
                            }
                    if reformulated_query:
                        artifacts["reformulated_query"] = reformulated_query
                    if reformulation:
                        artifacts["reformulation_trace"] = reformulation.to_dict()
                    from core.drill import read_last_proactive_offer_at
                    cognitive_trigger = (
                        select_cognitive_trigger(
                            history=recent,
                            profile=load_profile_payload(state_root, repo=repo),
                            drill_pending=profile.pending_triggers.get("review_drill"),
                            drill_history=load_drill_history(state_root) or profile.drill_due,
                            intent={"detected_intent": decision.mode},
                            pending_triggers=profile.pending_triggers,
                            uncertain_concepts=profile.uncertain_concepts,
                            last_drill_offer_at=read_last_proactive_offer_at(state_root),
                        )
                        if event_mode not in ("development", "test")
                        else {"trigger_type": "none"}
                    )
                    if cognitive_trigger.get("trigger_type") == "self_assessment":
                        from datetime import datetime, timedelta, timezone

                        pending = load_pending_triggers(state_root)
                        trigger_to_store = dict(cognitive_trigger)
                        issued_at = datetime.now(timezone.utc)
                        trigger_to_store.setdefault("issued_at", issued_at.isoformat())
                        trigger_to_store.setdefault(
                            "expires_at", (issued_at + timedelta(hours=24)).isoformat()
                        )
                        pending["self_assessment"] = trigger_to_store
                        write_pending_triggers_atomic(pending, state_root)
                        artifacts["cognitive_trigger"] = trigger_to_store
                    elif cognitive_trigger.get("trigger_type") == "review_drill":
                        from core.drill import build_review_offer_if_due

                        offer = build_review_offer_if_due(
                            learner_id=learner_id,
                            state_root=state_root,
                        )
                        if offer:
                            cognitive_trigger = {
                                **cognitive_trigger,
                                "payload": {
                                    **(cognitive_trigger.get("payload") or {}),
                                    "concept_id": offer.concept_id,
                                    "drill_session_id": offer.drill_session_id,
                                },
                                "markdown": (
                                    "## 복습 드릴\n"
                                    f"- 이전 드릴을 다시 풀어볼 차례야: {offer.question}"
                                ),
                            }
                            artifacts["drill_offer"] = {
                                "concept_id": offer.concept_id,
                                "question": offer.question,
                                "expected_terms": offer.expected_terms,
                                "source": offer.source,
                            }
                        artifacts["cognitive_trigger"] = cognitive_trigger
                    elif cognitive_trigger.get("trigger_type") == "follow_up":
                        artifacts["cognitive_trigger"] = cognitive_trigger
                    elif cognitive_trigger.get("trigger_type") == "proactive_drill":
                        from core.drill import (
                            build_offer_if_due, write_last_proactive_offer_at,
                        )

                        offer = build_offer_if_due(
                            learner_id=learner_id, state_root=state_root,
                            uncertain_concept_ids=profile.uncertain_concepts or [],
                        )
                        if offer:
                            artifacts["drill_offer"] = {
                                "concept_id": offer.concept_id,
                                "question": offer.question,
                                "expected_terms": offer.expected_terms,
                                "source": offer.source,
                            }
                            artifacts["cognitive_trigger"] = cognitive_trigger
                            write_last_proactive_offer_at(offer.created_at, state_root)
                        # else: no resolvable question -> drop the trigger silently.
                    from core.response_capture import (
                        create_pending_capture,
                        is_internal_capture_meta_prompt,
                    )
                    internal_meta_prompt = is_internal_capture_meta_prompt(prompt)
                    # Phase Y11 F8: event_id is generated BEFORE compose so the
                    # response_quality_hint.command_template can embed it and
                    # the AI session's follow-up wrapper call joins on the
                    # same id as the history event.
                    # The uuid suffix guarantees uniqueness: the serve loop is
                    # serial but the warm/exact-shortcut path can complete two
                    # asks within the same millisecond, and event_id is also the
                    # pending-capture filename ({event_id}.json) — a ms+pid
                    # collision would silently overwrite a sibling's capture.
                    # Matches the {prefix}-{ms}-{uuid} convention in code_event.
                    event_id = f"ask-{int(time.time() * 1000)}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
                    # Phase Y11 P1.2 — derive learner_context from v3 profile
                    # (mastered + proficient → must_skip_explanations_of).
                    # W7: subtract self-declared-beginner concepts so a concept the
                    # learner says they're new to is always re-explained, even if
                    # mastery over-promoted it. Caller-provided learner_context wins.
                    derived_context = req.get("learner_context")
                    if derived_context is None:
                        beginner = set(
                            getattr(profile, "self_declared_beginner_concepts", []) or []
                        )
                        skip_targets = sorted(
                            (set(profile.mastered_concepts)
                             | set(getattr(profile, "proficient_concepts", [])))
                            - beginner
                        )
                        if skip_targets:
                            derived_context = {
                                "must_skip_explanations_of": skip_targets,
                            }
                    markdown, response_hints, response_quality_hint, effective_route = compose(
                        decision, artifacts, prompt,
                        repo=repo, learner_id=learner_id, recent_history=recent,
                        source_event_id=None if internal_meta_prompt else event_id,
                        state_root=state_root,
                        learner_context=derived_context,
                    )
                    latency_ms = round((time.perf_counter() - ask_started) * 1000, 1)
                    event = {
                        "event_id": event_id,
                        "ts": time.time(),
                        "event_type": "rag_ask",
                        "mode": event_mode,
                        "mode_source": event_mode_source,
                        "learner_id": learner_id,
                        "repo": repo,
                        "payload": {
                            "prompt": prompt,
                            "repo": repo,
                            "raw_utterance": req.get("raw_utterance") or None,
                            "router_mode": effective_route.mode,
                            "router_reason": effective_route.reason,
                            "reformulated_query": reformulated_query or None,
                            "reformulation_source": (
                                reformulation.source if reformulation else None
                            ),
                            "top_concept_ids": list(response_hints["citation_paths"]),
                            "latency_ms": latency_ms,
                        },
                    }
                    try:
                        if not internal_meta_prompt:
                            append_history_event(event, state_root=state_root)
                            # A pending capture exists to record the learner's answer
                            # to a genuine learning turn — one carrying a real learner
                            # utterance (--raw-utterance, CLAUDE.md §4.2). A bare bin/ask
                            # probe or a multi-intent sub-ask (no raw_utterance) must NOT
                            # create one: the Stop hook attaches the session's NEXT final
                            # message to latest_pending_capture() without verifying it
                            # answers that turn, so an ungated probe pending silently
                            # absorbs unrelated dev-work narration. Mirrors the
                            # event_mode=="learning" gate record_turn already applies.
                            if _is_capturable_learning_turn(event_mode, req.get("raw_utterance")):
                                create_pending_capture(
                                    event,
                                    state_root=state_root,
                                    expected_citation_paths=list(response_hints["citation_paths"]),
                                )
                        if event_mode == "learning" and not internal_meta_prompt:
                            record_turn(event, state_root=state_root)
                    except Exception as exc:  # noqa: BLE001
                        # Telemetry/evidence must not break the response, but a
                        # persistent failure (disk full, perms) would otherwise be
                        # silent — surface to stderr (→ daemon --log-path).
                        print(
                            f"[telemetry] event_id={event_id} capture failed: {exc!r}",
                            file=sys.stderr,
                        )
                    payload = {
                        "markdown": markdown,
                        "mode": effective_route.mode,
                        "budget": effective_route.budget_tokens,
                        "personas": effective_route.personas,
                        "reason": effective_route.reason,
                        "event_id": None if internal_meta_prompt else event_id,
                        "latency_ms": latency_ms,
                        "response_hints": response_hints,
                        "response_quality_hint": response_quality_hint,
                    }
                    if action == "ask_text":
                        conn.sendall(
                            _render_ask_stdout(
                                payload,
                                json_route=bool(req.get("json_route")),
                            ).encode("utf-8")
                        )
                    else:
                        conn.sendall(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
                else:
                    conn.sendall(json.dumps({"error": f"unknown action {action}"}).encode() + b"\n")
            except Exception as exc:  # noqa: BLE001
                _safe_send(conn, json.dumps({"error": str(exc)}).encode() + b"\n")
            finally:
                conn.close()
    finally:
        srv.close()
        sp.unlink(missing_ok=True)
        pid_p.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "start-bg", "stop", "ping", "status"))
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--timeout-s", type=float, default=90.0)
    args = parser.parse_args(argv)
    if args.action == "start":
        serve(state_root=args.state_root)
    elif args.action == "start-bg":
        ok = start_background(
            state_root=args.state_root,
            log_path=args.log_path,
            timeout_s=args.timeout_s,
        )
        print("started" if ok else f"not ready; see {args.log_path}")
        return 0 if ok else 1
    elif args.action == "stop":
        print("stopped" if stop(args.state_root) else "not running")
    elif args.action == "ping":
        print("alive" if ping(args.state_root) else "no response")
    elif args.action == "status":
        sp = _socket_path(args.state_root)
        print(f"socket exists: {sp.exists()}, ping: {ping(args.state_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
