"""Daemon latency measurement harness (Y13-0).

Records p50/p95 for the layers that matter to learner-facing behavior:
daemon socket ask, CLI E2E ask, cold daemon readiness, and first ask after
cold start. Expensive cold-start probes are opt-in.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_PATH = REPO_ROOT / "reports" / "y13_latency_baseline.json"
DEFAULT_PROMPT = "DI"
DEFAULT_LEGACY_ROOT = REPO_ROOT.parent / "woowa-learning-hub"


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * q) - 1))
    return ordered[idx]


def _summary(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50_ms": round(statistics.median(values), 1) if values else None,
        "p95_ms": round(_percentile(values, 0.95), 1) if values else None,
        "min_ms": round(min(values), 1) if values else None,
        "max_ms": round(max(values), 1) if values else None,
    }


def _timed_run(
    cmd: list[str],
    *,
    cwd: Path = REPO_ROOT,
    timeout_s: int | float,
    env: dict[str, str] | None = None,
) -> tuple[float, subprocess.CompletedProcess[str]]:
    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
        env=env,
    )
    return (time.perf_counter() - t0) * 1000, proc


def _socket_ask(prompt: str, timeout_s: int) -> tuple[float, dict]:
    sock_path = REPO_ROOT / "state" / "rag-daemon.sock"
    t0 = time.perf_counter()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    sock.connect(str(sock_path))
    req = json.dumps({"action": "ask", "query": prompt, "learner_id": "default"}) + "\n"
    sock.sendall(req.encode("utf-8"))
    data = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    sock.close()
    ms = (time.perf_counter() - t0) * 1000
    return ms, json.loads(data.decode("utf-8").strip())


def _cli_ask(prompt: str, timeout_s: int) -> tuple[float, dict]:
    ms, proc = _timed_run(
        ["bin/ask", prompt, "--json"],
        timeout_s=timeout_s,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-200:] or f"bin/ask rc={proc.returncode}")
    return ms, json.loads(proc.stdout)


def _ping(timeout_s: float = 2.0) -> bool:
    sock_path = REPO_ROOT / "state" / "rag-daemon.sock"
    if not sock_path.exists():
        return False
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout_s)
        sock.connect(str(sock_path))
        sock.sendall(b'{"action":"ping"}\n')
        data = sock.recv(1024)
        sock.close()
        return json.loads(data.decode("utf-8")).get("alive") is True
    except (OSError, json.JSONDecodeError):
        return False


def _stop_daemon() -> None:
    subprocess.run(
        ["bin/rag-daemon", "stop"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )


def _start_daemon_and_wait(timeout_s: int, log_path: Path) -> float:
    t0 = time.perf_counter()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    subprocess.Popen(
        ["bin/rag-daemon", "start"],
        cwd=REPO_ROOT,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if _ping(timeout_s=2.0):
            return (time.perf_counter() - t0) * 1000
        time.sleep(0.05)
    raise TimeoutError(f"daemon did not become ready within {timeout_s}s; log={log_path}")


def _read_startup_timings(log_path: Path) -> dict | None:
    prefix = "[daemon] startup_timings "
    if not log_path.exists():
        return None
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if prefix in line:
            payload = line.split(prefix, 1)[1].strip()
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                return None
            return data if isinstance(data, dict) else None
    return None


def _legacy_bin(root: Path, name: str) -> str:
    return str(root / "bin" / name)


def _legacy_stop(root: Path) -> None:
    subprocess.run(
        [_legacy_bin(root, "rag-daemon"), "stop"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )


def _legacy_status(root: Path, timeout_s: float = 5.0) -> dict:
    proc = subprocess.run(
        [_legacy_bin(root, "rag-daemon"), "status"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-200:] or f"legacy rag-daemon status rc={proc.returncode}")
    return json.loads(proc.stdout)


def _legacy_prewarm_complete(root: Path, timeout_s: int) -> tuple[float, dict]:
    t0 = time.perf_counter()
    deadline = time.perf_counter() + timeout_s
    last_status: dict = {}
    while time.perf_counter() < deadline:
        last_status = _legacy_status(root)
        prewarm = (last_status.get("health") or {}).get("prewarm") or {}
        if prewarm.get("complete") is True:
            return (time.perf_counter() - t0) * 1000, prewarm
        time.sleep(0.25)
    raise TimeoutError("legacy daemon prewarm did not complete")


def _legacy_ask(root: Path, prompt: str, timeout_s: int) -> tuple[float, dict]:
    ms, proc = _timed_run(
        [_legacy_bin(root, "rag-ask"), prompt],
        cwd=root,
        timeout_s=timeout_s,
        env={**os.environ, "WOOWA_SESSION_MODE": "development"},
    )
    return ms, {
        "rc": proc.returncode,
        "stdout_tail": proc.stdout[-500:],
        "stderr_tail": proc.stderr[-500:],
    }


def _measure_legacy_health(root: Path, samples: int, prompt: str, timeout_s: int) -> dict:
    health_values: list[float] = []
    ask_values: list[float] = []
    total_values: list[float] = []
    errors: list[str] = []
    rows: list[dict] = []
    for _ in range(samples):
        try:
            _legacy_stop(root)
            health_ms, start_proc = _timed_run(
                [_legacy_bin(root, "rag-daemon"), "start", "--timeout", str(timeout_s)],
                cwd=root,
                timeout_s=timeout_s,
                env={**os.environ, "WOOWA_SESSION_MODE": "development"},
            )
            if start_proc.returncode != 0:
                raise RuntimeError(start_proc.stderr[-200:] or "legacy start failed")
            ask_ms, ask_payload = _legacy_ask(root, prompt, timeout_s)
            if ask_payload["rc"] != 0:
                raise RuntimeError(ask_payload["stderr_tail"] or "legacy rag-ask failed")
            health_values.append(health_ms)
            ask_values.append(ask_ms)
            total_values.append(health_ms + ask_ms)
            rows.append({
                "health_ready_ms": round(health_ms, 1),
                "first_ask_after_health_ms": round(ask_ms, 1),
                "total_to_first_answer_after_health_ms": round(health_ms + ask_ms, 1),
            })
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}"[:200])
    return {
        "label": "legacy_health",
        "layers": {
            "health_ready": _summary(health_values),
            "first_ask_after_health": _summary(ask_values),
            "total_to_first_answer_after_health": _summary(total_values),
        },
        "rows": rows,
        "errors": errors,
        "pass": not errors and bool(rows),
    }


def _measure_legacy_prewarm(
    root: Path,
    samples: int,
    prompt: str,
    timeout_s: int,
    warm_cli: int,
) -> dict:
    health_values: list[float] = []
    prewarm_wait_values: list[float] = []
    prewarm_total_values: list[float] = []
    ask_values: list[float] = []
    total_values: list[float] = []
    warm_values: list[float] = []
    errors: list[str] = []
    rows: list[dict] = []
    prewarm_tasks: list[dict] = []
    for _ in range(samples):
        try:
            _legacy_stop(root)
            health_ms, start_proc = _timed_run(
                [_legacy_bin(root, "rag-daemon"), "start", "--timeout", str(timeout_s)],
                cwd=root,
                timeout_s=timeout_s,
                env={**os.environ, "WOOWA_SESSION_MODE": "development"},
            )
            if start_proc.returncode != 0:
                raise RuntimeError(start_proc.stderr[-200:] or "legacy start failed")
            wait_ms, prewarm = _legacy_prewarm_complete(root, timeout_s)
            ask_ms, ask_payload = _legacy_ask(root, prompt, timeout_s)
            if ask_payload["rc"] != 0:
                raise RuntimeError(ask_payload["stderr_tail"] or "legacy rag-ask failed")
            health_values.append(health_ms)
            prewarm_wait_values.append(wait_ms)
            prewarm_total_values.append(health_ms + wait_ms)
            ask_values.append(ask_ms)
            total_values.append(health_ms + wait_ms + ask_ms)
            prewarm_tasks.append(prewarm.get("tasks", {}))
            row = {
                "health_ready_ms": round(health_ms, 1),
                "prewarm_wait_after_health_ms": round(wait_ms, 1),
                "prewarm_complete_total_ms": round(health_ms + wait_ms, 1),
                "first_ask_after_prewarm_ms": round(ask_ms, 1),
                "total_to_first_answer_after_prewarm_ms": round(health_ms + wait_ms + ask_ms, 1),
            }
            rows.append(row)
            for _ in range(warm_cli):
                warm_ms, warm_payload = _legacy_ask(root, prompt, timeout_s)
                if warm_payload["rc"] != 0:
                    raise RuntimeError(warm_payload["stderr_tail"] or "legacy warm ask failed")
                warm_values.append(warm_ms)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}"[:200])
    return {
        "label": "legacy_prewarm",
        "layers": {
            "health_ready": _summary(health_values),
            "prewarm_wait_after_health": _summary(prewarm_wait_values),
            "prewarm_complete_total": _summary(prewarm_total_values),
            "first_ask_after_prewarm": _summary(ask_values),
            "total_to_first_answer_after_prewarm": _summary(total_values),
            "warm_ask": _summary(warm_values),
        },
        "rows": rows,
        "prewarm_tasks": prewarm_tasks,
        "errors": errors,
        "pass": not errors and bool(rows),
    }


def _measure_legacy(args: argparse.Namespace) -> dict:
    root = args.legacy_root
    if root is None:
        return {}
    if not (_legacy_bin(root, "rag-daemon")) or not (root / "bin" / "rag-daemon").exists():
        return {"available": False, "reason": f"legacy bin/rag-daemon missing: {root}"}
    if not (root / "bin" / "rag-ask").exists():
        return {"available": False, "reason": f"legacy bin/rag-ask missing: {root}"}
    samples = args.legacy_samples or max(args.cold_start, args.first_ask, 1)
    report: dict = {
        "available": True,
        "legacy_root": str(root),
        "samples": samples,
        "wait_mode": args.legacy_wait,
        "notes": {
            "health_ready": "legacy start returned running /health; prewarm may still be running",
            "prewarm_complete_total": "health_ready + wait until health.prewarm.complete",
            "total_to_first_answer": "daemon start/wait + measured rag-ask wall time",
        },
    }
    if args.legacy_wait in ("health", "both"):
        report["health_scenario"] = _measure_legacy_health(
            root, samples, args.prompt, args.cold_timeout_s,
        )
    if args.legacy_wait in ("prewarm", "both"):
        report["prewarm_scenario"] = _measure_legacy_prewarm(
            root, samples, args.prompt, args.cold_timeout_s, args.legacy_warm_cli,
        )
    report["pass"] = all(
        scenario.get("pass", False)
        for key, scenario in report.items()
        if key.endswith("_scenario")
    )
    return report


def _measure_repeated(label: str, count: int, fn) -> dict:
    values: list[float] = []
    errors: list[str] = []
    for _ in range(count):
        try:
            ms, payload = fn()
            if not payload.get("event_id"):
                errors.append("missing event_id")
            values.append(ms)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}"[:200])
    summary = _summary(values)
    summary.update({"label": label, "errors": errors, "pass": not errors and bool(values)})
    return summary


def measure(args: argparse.Namespace) -> dict:
    report: dict = {
        "benchmark": "daemon_latency",
        "prompt": args.prompt,
        "timestamp": time.time(),
        "layers": {},
        "startup_timings": {},
    }

    cold_values: list[float] = []
    cold_errors: list[str] = []
    cold_startup_timings: list[dict] = []
    for i in range(args.cold_start):
        try:
            log_path = Path("/tmp") / f"woowa-daemon-latency-cold-{i}.log"
            _stop_daemon()
            ms = _start_daemon_and_wait(
                args.cold_timeout_s,
                log_path,
            )
            cold_values.append(ms)
            timing = _read_startup_timings(log_path)
            if timing:
                cold_startup_timings.append(timing)
        except Exception as exc:  # noqa: BLE001
            cold_errors.append(f"{type(exc).__name__}: {exc}"[:200])
    if args.cold_start:
        layer = _summary(cold_values)
        layer.update({"label": "cold_start", "errors": cold_errors, "pass": not cold_errors})
        report["layers"]["cold_start"] = layer
        report["startup_timings"]["cold_start"] = cold_startup_timings

    first_values: list[float] = []
    first_ready_values: list[float] = []
    first_total_values: list[float] = []
    first_errors: list[str] = []
    first_startup_timings: list[dict] = []
    for i in range(args.first_ask):
        try:
            log_path = Path("/tmp") / f"woowa-daemon-latency-first-{i}.log"
            _stop_daemon()
            ready_ms = _start_daemon_and_wait(
                args.cold_timeout_s,
                log_path,
            )
            ms, payload = _cli_ask(args.prompt, args.timeout_s)
            if not payload.get("event_id"):
                first_errors.append("missing event_id")
            first_ready_values.append(ready_ms)
            first_values.append(ms)
            first_total_values.append(ready_ms + ms)
            timing = _read_startup_timings(log_path)
            if timing:
                first_startup_timings.append(timing)
        except Exception as exc:  # noqa: BLE001
            first_errors.append(f"{type(exc).__name__}: {exc}"[:200])
    if args.first_ask:
        report["startup_timings"]["first_ask"] = first_startup_timings
        ready_layer = _summary(first_ready_values)
        ready_layer.update({"label": "first_ready", "errors": first_errors, "pass": not first_errors})
        report["layers"]["first_ready"] = ready_layer

        layer = _summary(first_values)
        layer.update({"label": "first_ask", "errors": first_errors, "pass": not first_errors})
        report["layers"]["first_ask"] = layer

        total_layer = _summary(first_total_values)
        total_layer.update({
            "label": "first_answer_total",
            "errors": first_errors,
            "pass": not first_errors,
            "definition": "daemon stop -> prewarm-ready -> first bin/ask answer",
        })
        report["layers"]["first_answer_total"] = total_layer

    if args.warm_socket:
        report["layers"]["warm_socket"] = _measure_repeated(
            "warm_socket",
            args.warm_socket,
            lambda: _socket_ask(args.prompt, args.timeout_s),
        )

    if args.warm_cli:
        report["layers"]["warm_cli"] = _measure_repeated(
            "warm_cli",
            args.warm_cli,
            lambda: _cli_ask(args.prompt, args.timeout_s),
        )

    if args.bootstrap:
        values: list[float] = []
        errors: list[str] = []
        for _ in range(args.bootstrap):
            t0 = time.perf_counter()
            proc = subprocess.run(
                ["bin/bootstrap"],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=args.cold_timeout_s,
                env={**os.environ, "WOOWA_SESSION_MODE": "development"},
            )
            values.append((time.perf_counter() - t0) * 1000)
            if proc.returncode != 0:
                errors.append(proc.stderr[-200:] or f"bin/bootstrap rc={proc.returncode}")
        layer = _summary(values)
        layer.update({"label": "bootstrap", "errors": errors, "pass": not errors})
        report["layers"]["bootstrap"] = layer

    if args.legacy_root is not None:
        report["legacy"] = _measure_legacy(args)

    report["pass"] = bool(report["layers"]) and all(
        layer.get("pass", False) for layer in report["layers"].values()
    )
    if "legacy" in report and report["legacy"]:
        report["legacy"]["comparison"] = _legacy_comparison(report)
    return report


def _legacy_comparison(report: dict) -> dict:
    first_total = (report.get("layers") or {}).get("first_answer_total") or {}
    legacy = report.get("legacy") or {}
    out: dict = {}
    prewarm = (legacy.get("prewarm_scenario") or {}).get("layers") or {}
    health = (legacy.get("health_scenario") or {}).get("layers") or {}
    v2_p95 = first_total.get("p95_ms")
    legacy_prewarm_p95 = (
        prewarm.get("total_to_first_answer_after_prewarm") or {}
    ).get("p95_ms")
    legacy_health_p95 = (
        health.get("total_to_first_answer_after_health") or {}
    ).get("p95_ms")
    if v2_p95 and legacy_prewarm_p95:
        out["total_first_answer_vs_legacy_prewarm_p95_speedup"] = round(
            legacy_prewarm_p95 / v2_p95, 2
        )
    if v2_p95 and legacy_health_p95:
        out["total_first_answer_vs_legacy_health_p95_speedup"] = round(
            legacy_health_p95 / v2_p95, 2
        )
    out["v2_first_answer_total_p95_ms"] = v2_p95
    out["legacy_prewarm_total_first_answer_p95_ms"] = legacy_prewarm_p95
    out["legacy_health_total_first_answer_p95_ms"] = legacy_health_p95
    return out


def _apply_layer_defaults(args: argparse.Namespace) -> None:
    if args.layer == "socket":
        args.warm_socket = args.warm_socket or args.iterations
    elif args.layer == "cli":
        args.warm_cli = args.warm_cli or args.iterations
    if not any((args.warm_socket, args.warm_cli, args.cold_start, args.first_ask, args.bootstrap)):
        args.warm_socket = args.iterations
        args.warm_cli = args.iterations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layer", choices=("socket", "cli"), default=None)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--warm-socket", type=int, default=0)
    parser.add_argument("--warm-cli", type=int, default=0)
    parser.add_argument("--cold-start", type=int, default=0)
    parser.add_argument("--first-ask", type=int, default=0)
    parser.add_argument("--bootstrap", type=int, default=0)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--timeout-s", type=int, default=30)
    parser.add_argument("--cold-timeout-s", type=int, default=45)
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--legacy-root", type=Path, default=None)
    parser.add_argument("--legacy-samples", type=int, default=0)
    parser.add_argument("--legacy-wait", choices=("health", "prewarm", "both"), default="prewarm")
    parser.add_argument("--legacy-warm-cli", type=int, default=3)
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)
    if args.legacy and args.legacy_root is None:
        args.legacy_root = DEFAULT_LEGACY_ROOT
    _apply_layer_defaults(args)

    report = measure(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output = {
        "layers": report["layers"],
        "pass": report["pass"],
        "report": str(args.out),
    }
    if report.get("legacy"):
        output["legacy_comparison"] = report["legacy"].get("comparison")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
