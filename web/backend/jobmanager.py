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
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import runs

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
JOBS_DIR = Path(__file__).resolve().parent / ".jobs"
JOBS_FILE = JOBS_DIR / "jobs.json"

CONDA_BIN = os.environ.get("CONDA_BIN", "conda")
CHRONO_ENV = os.environ.get("CHRONO_ENV", "chrono")

_RUN_DIR_RE = re.compile(r"(?:New run directory|Resuming run directory|Reusing physics from):\s*(\S+)")

_lock = threading.Lock()
_jobs: Dict[int, Dict[str, Any]] = {}
# Live process handles for running jobs, so they can be cancelled.
_procs: Dict[int, subprocess.Popen] = {}
# Job ids the user asked to cancel (distinguishes a kill from a real failure).
_cancelled: set[int] = set()

# Jobs run one-at-a-time through a single worker: the pipeline is GPU-bound and
# two concurrent renders just contend for the one GPU (OptiX). Queued jobs sit
# in "pending" until the worker picks them up.
_work_queue: "queue.Queue[int]" = queue.Queue()
_worker_thread: Optional[threading.Thread] = None


def _ensure_worker() -> None:
    global _worker_thread
    with _lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        _worker_thread = threading.Thread(target=_worker_loop, daemon=True)
        _worker_thread.start()


def _worker_loop() -> None:
    while True:
        job_id = _work_queue.get()
        try:
            with _lock:
                job = _jobs.get(job_id)
                # Skip jobs cancelled while still queued, or otherwise gone.
                if job is None or job["status"] != "pending":
                    continue
                args = job["args"]
            _run(job_id, args)
        except Exception:
            # A failure inside one job must not kill the worker.
            with _lock:
                job = _jobs.get(job_id)
                if job is not None and not is_terminal(job["status"]):
                    job.update(status="failed", error="worker error", finished_at=_utc_now())
                    _persist()
        finally:
            _work_queue.task_done()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _persist() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    # Write-then-rename so a crash mid-write can't corrupt jobs.json.
    tmp = JOBS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(list(_jobs.values()), indent=2))
    os.replace(tmp, JOBS_FILE)


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
            # Anything mid-flight (running) or still queued (pending) is dead now:
            # its thread/queue didn't survive the restart. Mark it interrupted.
            if rec.get("status") in ("running", "pending"):
                rec["status"] = "interrupted"
                rec["error"] = "backend restarted while job was running"
            _jobs[rec["id"]] = rec


def _next_id() -> int:
    return (max(_jobs.keys()) + 1) if _jobs else 1


def build_args(req: Dict[str, Any]) -> List[str]:
    """Translate a job request into run.py CLI args."""
    args: List[str] = []
    resume = req.get("resume_run_id")
    if resume is not None:
        # Render into an existing run (e.g. from an edited Blender scene); its
        # physics/config are reused, so bodies/seconds don't apply.
        args += ["-r", str(runs.run_dir_for(int(resume)))]
    if req.get("quality"):
        args += ["-q", str(req["quality"])]
    if resume is None and req.get("num_bodies") is not None:
        args += ["-n", str(int(req["num_bodies"]))]
    if resume is None and req.get("seconds") is not None:
        args += ["-t", str(float(req["seconds"]))]
    if req.get("blender_scene"):
        args += ["-b", str(req["blender_scene"])]
    if req.get("prep_scene"):
        args += ["-p"]
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
            # Original request, kept so the job can be re-run verbatim.
            "request": {
                k: req.get(k)
                for k in (
                    "quality", "num_bodies", "seconds", "first_frame", "config_override",
                    "name", "prep_scene", "resume_run_id", "blender_scene",
                )
            },
            "status": "pending",
            "scene_path": None,
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

    # Hand off to the single serial worker (starts it if needed).
    _ensure_worker()
    _work_queue.put(job_id)
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
                # Own session/process group so cancel can kill the whole tree
                # (conda run -> python run.py -> blender), not just the wrapper.
                start_new_session=True,
            )
            with _lock:
                _procs[job_id] = proc
            for line in proc.stdout:  # universal-newline: \r progress also yields lines
                logf.write(line)
                if run_dir is None:
                    m = _RUN_DIR_RE.search(line)
                    if m:
                        run_dir = m.group(1)
            rc = proc.wait()
    except Exception as exc:  # spawn failure, etc.
        with _lock:
            _procs.pop(job_id, None)
            _jobs[job_id].update(status="failed", error=str(exc), finished_at=_utc_now())
            _persist()
        return
    finally:
        with _lock:
            _procs.pop(job_id, None)

    if job_id in _cancelled:
        _cancelled.discard(job_id)
        with _lock:
            _jobs[job_id].update(
                status="cancelled",
                returncode=rc,
                finished_at=_utc_now(),
                run_dir=run_dir,
                error="cancelled by user",
            )
            _persist()
        return

    run_id = None
    if run_dir:
        name = Path(run_dir).name
        m = re.match(r"output(\d+)$", name)
        if m:
            run_id = int(m.group(1))

    # If this was a scene-prep job, surface the editable .blend it produced.
    scene_path = None
    if run_dir and _jobs[job_id]["request"].get("prep_scene"):
        candidate = Path(run_dir) / "scene_edit.blend"
        if candidate.exists():
            scene_path = str(candidate)

    with _lock:
        _jobs[job_id].update(
            status="success" if rc == 0 else "failed",
            returncode=rc,
            finished_at=_utc_now(),
            run_dir=run_dir,
            run_id=run_id,
            scene_path=scene_path,
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
    return status in ("success", "failed", "interrupted", "cancelled")


def active_jobs() -> List[Dict[str, Any]]:
    """Jobs that are currently working (queued or running) — for liveness."""
    with _lock:
        return [dict(j) for j in _jobs.values() if j["status"] in ("running", "pending")]


def cancel_job(job_id: int) -> Dict[str, Any]:
    """Terminate a running job's whole process tree (conda -> python -> blender)."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if is_terminal(job["status"]):
            return dict(job)
        proc = _procs.get(job_id)
        _cancelled.add(job_id)
        if proc is None:
            # Pending (thread not spawned proc yet) or already gone — mark cancelled.
            job.update(status="cancelled", finished_at=_utc_now(), error="cancelled by user")
            _persist()
            return dict(job)

    # Kill the process group; escalate to SIGKILL if it lingers.
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        for _ in range(20):
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        if proc.poll() is None:
            os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass

    return get_job(job_id) or {}


def delete_job(job_id: int) -> bool:
    """Remove a job record plus its log, override file, and output run dir."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job["status"] in ("running", "pending"):
            raise ValueError("cancel the job before deleting it")
        job = _jobs.pop(job_id)
        _persist()

    # Filesystem cleanup (best-effort, outside the lock).
    log_path = job.get("log_path")
    if log_path:
        Path(log_path).unlink(missing_ok=True)
    (JOBS_DIR / f"job_{job_id}_override.json").unlink(missing_ok=True)

    run_dir = job.get("run_dir")
    if run_dir:
        rd = Path(run_dir).resolve()
        out_root = runs.OUTPUT_ROOT.resolve()
        # Safety: only ever rmtree something that lives under the output root.
        if out_root in rd.parents and rd.is_dir():
            shutil.rmtree(rd, ignore_errors=True)
    return True


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
