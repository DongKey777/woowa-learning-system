"""Daemon latency measurement harness (Y13-0).

Records p50/p95 for the layers that matter to learner-facing behavior:
daemon socket ask, CLI E2E ask, cold daemon readiness, and first ask after
cold start. Expensive cold-start probes are opt-in.
"""
from __future__ import annotations

import argparse
import json
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


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * q)))
    return ordered[idx]


def _summary(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50_ms": round(statistics.median(values), 1) if values else None,
        "p95_ms": round(_percentile(values, 0.95), 1) if values else None,
        "min_ms": round(min(values), 1) if values else None,
        "max_ms": round(max(values), 1) if values else None,
    }


def _socket_ask(prompt: str, timeout_s: int) -> tuple[float, dict]:
    sock_path = REPO_ROOT / "state" / "rag-daemon.sock"
    t0 = time.perf_counter()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    sock.connect(str(sock_path))
    req = json.dumps({"action": "ask", "query": prompt, "learner_id": "default"}) + "\n"
    sock.sendall(req.encode("utf-8"))
    sock.shutdown(socket.SHUT_WR)
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
    t0 = time.perf_counter()
    proc = subprocess.run(
        ["bin/ask", prompt, "--json"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
    )
    ms = (time.perf_counter() - t0) * 1000
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-200:] or f"bin/ask rc={proc.returncode}")
    return ms, json.loads(proc.stdout)


def _ping(timeout_s: float = 2.0) -> bool:
    proc = subprocess.run(
        ["bin/rag-daemon", "ping"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
    )
    return proc.returncode == 0 and "alive" in proc.stdout


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
        time.sleep(0.25)
    raise TimeoutError(f"daemon did not become ready within {timeout_s}s; log={log_path}")


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
    }

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

    cold_values: list[float] = []
    cold_errors: list[str] = []
    for i in range(args.cold_start):
        try:
            _stop_daemon()
            ms = _start_daemon_and_wait(
                args.cold_timeout_s,
                Path("/tmp") / f"woowa-daemon-latency-cold-{i}.log",
            )
            cold_values.append(ms)
        except Exception as exc:  # noqa: BLE001
            cold_errors.append(f"{type(exc).__name__}: {exc}"[:200])
    if args.cold_start:
        layer = _summary(cold_values)
        layer.update({"label": "cold_start", "errors": cold_errors, "pass": not cold_errors})
        report["layers"]["cold_start"] = layer

    first_values: list[float] = []
    first_errors: list[str] = []
    for i in range(args.first_ask):
        try:
            _stop_daemon()
            _start_daemon_and_wait(
                args.cold_timeout_s,
                Path("/tmp") / f"woowa-daemon-latency-first-{i}.log",
            )
            ms, payload = _cli_ask(args.prompt, args.timeout_s)
            if not payload.get("event_id"):
                first_errors.append("missing event_id")
            first_values.append(ms)
        except Exception as exc:  # noqa: BLE001
            first_errors.append(f"{type(exc).__name__}: {exc}"[:200])
    if args.first_ask:
        layer = _summary(first_values)
        layer.update({"label": "first_ask", "errors": first_errors, "pass": not first_errors})
        report["layers"]["first_ask"] = layer

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

    report["pass"] = bool(report["layers"]) and all(
        layer.get("pass", False) for layer in report["layers"].values()
    )
    return report


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
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)
    _apply_layer_defaults(args)

    report = measure(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "layers": report["layers"],
        "pass": report["pass"],
        "report": str(args.out),
    }, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
