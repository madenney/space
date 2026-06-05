"""Async job manager: spawns run.py through the chrono conda env and tracks it.

Jobs run in background threads. Output is streamed (line-buffered) to a per-job
log file so multiple SSE clients can tail the same job. Job records are kept in
memory and mirrored to .jobs/jobs.json so they survive a backend restart.

We must go through `conda run` because the chrono env relies on activation hooks
(invoking the env's python directly fails to import pychrono). `--no-capture-output`
makes conda stream child stdout/stderr instead of buffering it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
JOBS_DIR = Path(__file__).resolve().parent / ".jobs"
JOBS_FILE = JOBS_DIR / "jobs.json"

CONDA_BIN = os.environ.get("CONDA_BIN", "conda")
CHRONO_ENV = os.environ.get("CHRONO_ENV", "chrono")

_RUN_DIR_RE = re.compile(r"(?:New run directory|Resuming run directory|Reusing physics from):\s*(\S+)")

_lock = threading.Lock()
_jobs: Dict[int, Dict[str, Any]] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _persist() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    # Don't persist transient fields; the record dicts are already JSON-safe.
    JOBS_FILE.write_text(json.dumps(list(_jobs.values()), indent=2))


def load_persisted() -> None:
    """Load past jobs on startup; mark anything left 'running' as interrupted."""
    if not JOBS_FILE.exists():
        return
    try:
        records = json.loads(JOBS_FILE.read_text())
    except Exception:
        return
    with _lock:
        for rec in records:
            if rec.get("status") == "running":
                rec["status"] = "interrupted"
                rec["error"] = "backend restarted while job was running"
            _jobs[rec["id"]] = rec


def _next_id() -> int:
    return (max(_jobs.keys()) + 1) if _jobs else 1


def build_args(req: Dict[str, Any]) -> List[str]:
    """Translate a job request into run.py CLI args."""
    args: List[str] = []
    if req.get("quality"):
        args += ["-q", str(req["quality"])]
    if req.get("num_bodies") is not None:
        args += ["-n", str(int(req["num_bodies"]))]
    if req.get("seconds") is not None:
        args += ["-t", str(float(req["seconds"]))]
    if req.get("first_frame"):
        args += ["-f"]
    return args


def create_job(req: Dict[str, Any]) -> Dict[str, Any]:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        job_id = _next_id()
        log_path = JOBS_DIR / f"job_{job_id}.log"
        log_path.write_text("")  # truncate/create

        args = build_args(req)
        override = req.get("config_override")
        override_path: Optional[Path] = None
        if override:
            override_path = JOBS_DIR / f"job_{job_id}_override.json"
            override_path.write_text(json.dumps(override, indent=2))
            args += ["-c", str(override_path)]

        job = {
            "id": job_id,
            "name": req.get("name") or f"job-{job_id}",
            "args": args,
            "status": "pending",
            "created_at": _utc_now(),
            "started_at": None,
            "finished_at": None,
            "returncode": None,
            "run_dir": None,
            "run_id": None,
            "error": None,
            "log_path": str(log_path),
        }
        _jobs[job_id] = job
        _persist()

    thread = threading.Thread(target=_run, args=(job_id, args), daemon=True)
    thread.start()
    return job


def _run(job_id: int, args: List[str]) -> None:
    log_path = Path(_jobs[job_id]["log_path"])
    cmd = [CONDA_BIN, "run", "--no-capture-output", "-n", CHRONO_ENV,
           "python", "-u", "run.py", *args]
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}

    with _lock:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = _utc_now()
        _persist()

    run_dir: Optional[str] = None
    try:
        with log_path.open("w", buffering=1) as logf:
            logf.write(f"[job] $ {' '.join(cmd)}\n")
            proc = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            for line in proc.stdout:  # universal-newline: \r progress also yields lines
                logf.write(line)
                if run_dir is None:
                    m = _RUN_DIR_RE.search(line)
                    if m:
                        run_dir = m.group(1)
            rc = proc.wait()
    except Exception as exc:  # spawn failure, etc.
        with _lock:
            _jobs[job_id].update(status="failed", error=str(exc), finished_at=_utc_now())
            _persist()
        return

    run_id = None
    if run_dir:
        name = Path(run_dir).name
        m = re.match(r"output(\d+)$", name)
        if m:
            run_id = int(m.group(1))

    with _lock:
        _jobs[job_id].update(
            status="success" if rc == 0 else "failed",
            returncode=rc,
            finished_at=_utc_now(),
            run_dir=run_dir,
            run_id=run_id,
            error=None if rc == 0 else f"exited with code {rc}",
        )
        _persist()


def list_jobs() -> List[Dict[str, Any]]:
    with _lock:
        return sorted(_jobs.values(), key=lambda j: j["id"], reverse=True)


def get_job(job_id: int) -> Optional[Dict[str, Any]]:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def is_terminal(status: str) -> bool:
    return status in ("success", "failed", "interrupted")


def tail_log(job_id: int):
    """Generator of SSE events: replays the log, then follows until the job ends."""
    job = get_job(job_id)
    if job is None:
        yield "event: error\ndata: job not found\n\n"
        return
    log_path = Path(job["log_path"])

    # Wait briefly for the log file to appear.
    for _ in range(50):
        if log_path.exists():
            break
        time.sleep(0.1)

    with log_path.open("r") as fh:
        while True:
            chunk = fh.readline()
            if chunk:
                # Strip trailing newline; SSE adds its own framing.
                yield f"data: {chunk.rstrip(chr(10))}\n\n"
                continue
            current = get_job(job_id)
            if current and is_terminal(current["status"]):
                # Drain any final bytes, then emit a done event.
                tail = fh.read()
                for line in tail.splitlines():
                    yield f"data: {line}\n\n"
                yield f"event: done\ndata: {current['status']}\n\n"
                return
            time.sleep(0.3)
