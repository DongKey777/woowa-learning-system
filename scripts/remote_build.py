"""Minimal RunPod build harness for woowa-learning-system.

Replaces legacy 12-step paramiko harness with 8-step system-ssh flow.
Build target: 3339 concepts × BGE-M3 dense → Lance index ≤20MB.

Steps:
  1. Pod create (A100 PCIe, 96GB RAM, runpod/pytorch:2.4 image)
  2. Wait SSH ready
  3. shallow fetch DongKey777/woowa-learning-system @ commit
  4. pip install --break-system-packages: lancedb pyarrow sentence-transformers
     FlagEmbedding jsonschema
  5. warm BGE-M3 (HF download)
  6. python -m bin.corpus-build → /workspace/state/index/
  7. tar + zstd → /workspace/index.tar.zst, scp to local
  8. Pod terminate (always — try/finally)

Usage:
  RUNPOD_API_KEY=$(cat ~/.runpod/api_key) python -m scripts.remote_build \\
      [--dry-run] [--keep-pod] [--commit-sha SHA]
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_URL = "https://github.com/DongKey777/woowa-learning-system.git"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
DEFAULT_GPU = "NVIDIA A100 80GB PCIe"
DEFAULT_CLOUD_TYPE = "COMMUNITY"
# ~3GB model cache + deps + ~150MB index; 25GB leaves headroom while staying
# deployable on small-disk community hosts (50GB tripped "machine lacks
# resources" on 24GB cards).
DEFAULT_DISK_GB = 25
DEFAULT_MIN_VRAM_GB = 40
# BGE-M3 encode of the corpus needs ~16GB host RAM; requiring 96GB filtered out
# every single-GPU instance (24-48GB cards pair with far less host RAM) and made
# RunPod return a generic "no instances available" for all GPUs/clouds.
DEFAULT_MIN_RAM_GB = 16

INDEX_LOCAL = REPO_ROOT / "state" / "index"
ARTIFACT_REMOTE = "/workspace/index.tar.zst"


@dataclass
class StepResult:
    name: str
    rc: int
    elapsed_s: float


def _ssh(host: str, port: int, cmd: str, *, timeout: int = 1800) -> StepResult:
    """Run cmd on Pod via system ssh (no paramiko)."""
    ssh_cmd = [
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=10",
        "-o", "ConnectTimeout=30", "-o", "LogLevel=ERROR",
        "-p", str(port), f"root@{host}", cmd,
    ]
    print(f"[ssh] {cmd[:160]}{'…' if len(cmd) > 160 else ''}", flush=True)
    t0 = time.perf_counter()
    rc = subprocess.call(ssh_cmd, timeout=timeout)
    return StepResult(name=cmd.split()[0], rc=rc, elapsed_s=time.perf_counter() - t0)


def _scp(host: str, port: int, remote: str, local: Path) -> StepResult:
    cmd = ["scp", "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=15",
           "-P", str(port), f"root@{host}:{remote}", str(local)]
    print(f"[scp] {remote} → {local}", flush=True)
    t0 = time.perf_counter()
    rc = subprocess.call(cmd, timeout=600)
    return StepResult(name="scp", rc=rc, elapsed_s=time.perf_counter() - t0)


def _current_commit_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
    ).strip()


def _build_commands(commit_sha: str) -> list[str]:
    return [
        # 3. system deps
        "apt-get update -qq && apt-get install -y -qq zstd git",
        # 4a. shallow fetch + checkout exact commit
        "git init /workspace/repo && cd /workspace/repo && "
        f"git remote add origin {REPO_URL} && "
        f"git fetch --depth 1 origin {commit_sha} && "
        "git checkout --detach FETCH_HEAD",
        # 4b. install deps (use Pod's CUDA-matched torch via --break-system-packages)
        "cd /workspace/repo && pip install --break-system-packages "
        "lancedb pyarrow numpy jsonschema sentence-transformers "
        "'transformers>=4.44.2,<4.50' 'FlagEmbedding==1.3.5'",
        # 5. warm BGE-M3 (fresh download on Pod's GPU image)
        "cd /workspace/repo && python -c \"from sentence_transformers import "
        "SentenceTransformer; SentenceTransformer('BAAI/bge-m3', device='cuda')\"",
        # 6. build index → state/index/
        "cd /workspace/repo && python bin/corpus-build "
        "--index-dir /workspace/repo/state/index",
        # 7. package
        f"cd /workspace/repo && tar -I 'zstd -19 -T0' -cf {ARTIFACT_REMOTE} "
        "-C state index && sha256sum " + ARTIFACT_REMOTE,
    ]


def run(
    api_key: str,
    *,
    commit_sha: str,
    keep_pod: bool,
    dry_run: bool,
    gpu_type: str,
    cloud_type: str,
    min_ram_gb: int = DEFAULT_MIN_RAM_GB,
    disk_gb: int = DEFAULT_DISK_GB,
) -> int:
    if dry_run:
        print("[dry-run] commands:")
        for i, cmd in enumerate(_build_commands(commit_sha)):
            print(f"  {i + 1}. {cmd}")
        print(f"[dry-run] would create pod ({gpu_type}, cloud={cloud_type}), execute steps, scp {ARTIFACT_REMOTE} → {INDEX_LOCAL}")
        return 0

    import runpod
    runpod.api_key = api_key

    print(f"[runpod] creating pod ({gpu_type}, cloud={cloud_type}, image={DEFAULT_IMAGE[:40]}…)")
    pod = runpod.create_pod(
        name=f"woowa-build-{int(time.time())}",
        image_name=DEFAULT_IMAGE,
        gpu_type_id=gpu_type,
        cloud_type=cloud_type,
        gpu_count=1,
        volume_in_gb=0,
        container_disk_in_gb=disk_gb,
        min_vcpu_count=4,
        min_memory_in_gb=min_ram_gb,
        ports="22/tcp",
        support_public_ip=True,
        start_ssh=True,
    )
    pod_id = pod["id"]
    print(f"[runpod] pod created: {pod_id}")

    def _terminate(signum=None, frame=None):
        if not keep_pod:
            try:
                runpod.terminate_pod(pod_id)
                print(f"[runpod] terminated pod {pod_id}")
            except Exception as exc:
                print(f"[runpod] terminate failed: {exc}", file=sys.stderr)
        if signum is not None:
            sys.exit(130)

    signal.signal(signal.SIGINT, _terminate)
    signal.signal(signal.SIGTERM, _terminate)
    results: list[StepResult] = []
    try:
        # wait for SSH
        for attempt in range(60):
            time.sleep(15)
            status = runpod.get_pod(pod_id)
            runtime = status.get("runtime") if status else None
            if runtime and runtime.get("ports"):
                for p in runtime["ports"]:
                    if p["privatePort"] == 22 and p.get("ip"):
                        host = p["ip"]
                        port = p["publicPort"]
                        break
                else:
                    continue
                break
            print(f"  [wait] attempt {attempt + 1}/60: pod not SSH-ready yet")
        else:
            raise TimeoutError("Pod SSH did not become ready in 15 min")
        print(f"[ssh] ready on {host}:{port}")

        for cmd in _build_commands(commit_sha):
            r = _ssh(host, port, cmd)
            results.append(r)
            print(f"[step] rc={r.rc} elapsed={r.elapsed_s:.1f}s")
            if r.rc != 0:
                print(f"[fatal] step failed: {cmd[:80]}…", file=sys.stderr)
                return 1

        # download artifact
        INDEX_LOCAL.parent.mkdir(parents=True, exist_ok=True)
        local_artifact = INDEX_LOCAL.parent / "index.tar.zst"
        scp_r = _scp(host, port, ARTIFACT_REMOTE, local_artifact)
        results.append(scp_r)
        if scp_r.rc != 0:
            return 2
        print(f"[done] artifact at {local_artifact}")
        print("Extract: tar -I zstd -xf state/index.tar.zst -C state/")
        return 0
    finally:
        _terminate()
        for r in results:
            print(f"  - {r.name}: rc={r.rc} ({r.elapsed_s:.1f}s)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-pod", action="store_true",
                        help="leave pod running after build (manual cleanup)")
    parser.add_argument("--commit-sha", default=None)
    parser.add_argument("--gpu-type", default=DEFAULT_GPU,
                        help=f"RunPod GPU type id (default: {DEFAULT_GPU})")
    parser.add_argument("--cloud-type", default=DEFAULT_CLOUD_TYPE,
                        choices=("COMMUNITY", "SECURE", "ALL"),
                        help=f"RunPod cloud type (default: {DEFAULT_CLOUD_TYPE})")
    parser.add_argument("--min-ram-gb", type=int, default=DEFAULT_MIN_RAM_GB,
                        help=f"min host RAM filter (default: {DEFAULT_MIN_RAM_GB})")
    parser.add_argument("--disk-gb", type=int, default=DEFAULT_DISK_GB,
                        help=f"container disk size (default: {DEFAULT_DISK_GB})")
    args = parser.parse_args(argv)

    api_key = os.environ.get("RUNPOD_API_KEY") or _read_key_file()
    if not api_key and not args.dry_run:
        parser.error("RUNPOD_API_KEY env not set, ~/.runpod/api_key not found")
    if args.commit_sha and not (7 <= len(args.commit_sha) <= 40 and all(c in "0123456789abcdef" for c in args.commit_sha.lower())):
        parser.error("--commit-sha must be a concrete hex git SHA")

    if args.commit_sha is None and subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, text=True
    ).strip():
        parser.error("dirty worktree; commit changes or pass --commit-sha")
    commit_sha = args.commit_sha or _current_commit_sha()
    return run(api_key=api_key or "", commit_sha=commit_sha,
               keep_pod=args.keep_pod, dry_run=args.dry_run,
               gpu_type=args.gpu_type, cloud_type=args.cloud_type,
               min_ram_gb=args.min_ram_gb, disk_gb=args.disk_gb)


def _read_key_file() -> str | None:
    p = Path.home() / ".runpod" / "api_key"
    if p.exists():
        return p.read_text().strip()
    return None


if __name__ == "__main__":
    raise SystemExit(main())
