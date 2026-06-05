#!/usr/bin/env python3
"""
Lightweight job queue for the render pipeline.

Usage examples:
  - Add a job:     python index.py add -q final --seconds 60
  - Show status:   python index.py status
  - Run worker:    python index.py worker --loop --poll-interval 10
    (run the worker in tmux/nohup to keep it alive; it processes one job at a time)
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - non-posix fallback
    fcntl = None


REPO_ROOT = Path(__file__).resolve().parent
QUEUE_DIR = REPO_ROOT / "queue"
QUEUE_FILE = QUEUE_DIR / "jobs.json"
LOG_DIR = QUEUE_DIR / "logs"
LOCK_FILE = QUEUE_DIR / "worker.lock"
OUTPUT_ROOT = REPO_ROOT / "output"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_jobs() -> List[Dict]:
    if not QUEUE_FILE.exists():
        return []
    try:
        return json.loads(QUEUE_FILE.read_text())
    except Exception:
        return []


def save_jobs(jobs: List[Dict]) -> None:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.write_text(json.dumps(jobs, indent=2))


def next_job_id(jobs: List[Dict]) -> int:
    if not jobs:
        return 1
    return max(job.get("id", 0) for job in jobs) + 1


def add_job(args_list: List[str], name: Optional[str] = None) -> Dict:
    jobs = load_jobs()
    job_id = next_job_id(jobs)
    job = {
        "id": job_id,
        "name": name or f"job-{job_id}",
        "args": args_list,
        "status": "pending",
        "created_at": utc_now(),
        "started_at": None,
        "finished_at": None,
        "returncode": None,
        "log_path": str(LOG_DIR / f"job_{job_id}.log"),
        "run_dir": None,
        "error": None,
    }
    jobs.append(job)
    save_jobs(jobs)
    return job


def print_status(jobs: List[Dict]) -> None:
    if not jobs:
        print("No jobs in queue.")
        return
    header = f"{'ID':>3}  {'Status':<8}  {'Name':<20}  {'Output':<12}  {'Args'}"
    print(header)
    print("-" * len(header))
    for job in sorted(jobs, key=lambda j: j.get("id", 0)):
        args = " ".join(job.get("args", []))
        status = job.get("status", "?")
        name = job.get("name", "")
        run_dir = Path(job.get("run_dir", "")).name if job.get("run_dir") else ""
        print(f"{job.get('id', 0):>3}  {status:<8}  {name:<20}  {run_dir:<12}  {args}")


def acquire_lock():
    if fcntl is None:
        return None
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fh = LOCK_FILE.open("w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.write(str(os.getpid()))
        fh.flush()
        return fh
    except BlockingIOError:
        fh.close()
        return None


def get_next_pending(jobs: List[Dict]) -> Optional[Dict]:
    pending = [j for j in jobs if j.get("status") == "pending"]
    if not pending:
        return None
    pending.sort(key=lambda j: (j.get("created_at", ""), j.get("id", 0)))
    return pending[0]


def latest_run_dir() -> Optional[Path]:
    if not OUTPUT_ROOT.exists():
        return None
    dirs = []
    for child in OUTPUT_ROOT.iterdir():
        if child.is_dir() and child.name.startswith("output"):
            suffix = child.name[len("output") :]
            if suffix.isdigit():
                dirs.append((int(suffix), child))
    if not dirs:
        return None
    dirs.sort(key=lambda x: x[0], reverse=True)
    return dirs[0][1]


def predict_next_run_dir() -> Path:
    """Predict the next output directory name (outputN) based on existing ones."""
    highest = 0
    if OUTPUT_ROOT.exists():
        for child in OUTPUT_ROOT.iterdir():
            if child.is_dir() and child.name.startswith("output"):
                suffix = child.name[len("output") :]
                if suffix.isdigit():
                    highest = max(highest, int(suffix))
    return OUTPUT_ROOT / f"output{highest + 1}"


def latest_log() -> Optional[Path]:
    candidates: List[Path] = []
    if OUTPUT_ROOT.exists():
        candidates.extend(p for p in OUTPUT_ROOT.glob("output*/run.log") if p.exists())
    jobs = load_jobs()
    for job in jobs:
        lp = Path(job.get("log_path", ""))
        if lp.exists():
            candidates.append(lp)
    if LOG_DIR.exists():
        candidates.extend(p for p in LOG_DIR.glob("job_*.log") if p.exists())
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def watch_log(log_path: Path) -> None:
    print(f"Watching {log_path} (Ctrl-C to exit)", flush=True)
    try:
        with log_path.open("r") as fh:
            cur = ""
            display_line = ""
            max_len = 0

            def render() -> None:
                nonlocal max_len
                max_len = max(max_len, len(display_line))
                pad = " " * (max_len - len(display_line))
                sys.stdout.write("\r" + display_line + pad)
                sys.stdout.flush()

            def process_chunk(chunk: str) -> None:
                nonlocal cur, display_line
                updated = False
                for ch in chunk:
                    if ch in ("\r", "\n"):
                        display_line = cur
                        cur = ""
                        updated = True
                    else:
                        cur += ch
                if cur:
                    display_line = cur
                    updated = True
                if updated:
                    render()

            existing = fh.read()
            if existing:
                process_chunk(existing)

            while True:
                chunk = fh.read(1024)
                if chunk:
                    process_chunk(chunk)
                else:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    except FileNotFoundError:
        print(f"Log not found: {log_path}", file=sys.stderr)
    


def update_job(jobs: List[Dict], job: Dict) -> None:
    for idx, existing in enumerate(jobs):
        if existing.get("id") == job.get("id"):
            jobs[idx] = job
            return


def run_job(job: Dict, jobs: List[Dict]) -> None:
    log_path = Path(job["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    predicted_run_dir = predict_next_run_dir()
    job["run_dir"] = str(predicted_run_dir)
    cmd = [sys.executable, str(REPO_ROOT / "run.py"), *job["args"]]
    job["status"] = "running"
    job["started_at"] = utc_now()
    job["error"] = None
    job["returncode"] = None
    update_job(jobs, job)
    save_jobs(jobs)
    with log_path.open("a") as log:
        log.write(f"[queue] Starting job {job['id']} at {job['started_at']}\n")
        log.write(f"[queue] Command: {' '.join(cmd)}\n")
        log.flush()
        proc = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=log,
            text=True,
            bufsize=1,
        )
        rc = proc.wait()
    job["returncode"] = rc
    job["finished_at"] = utc_now()
    job["status"] = "success" if rc == 0 else "failed"
    # Update run_dir to actual latest if prediction missed
    latest_dir = latest_run_dir()
    if latest_dir is not None:
        job["run_dir"] = str(latest_dir)
    if rc != 0:
        job["error"] = f"Process exited with {rc}"
    update_job(jobs, job)
    save_jobs(jobs)


def worker(loop: bool, poll_interval: int) -> None:
    lock = acquire_lock()
    if fcntl and lock is None:
        print("Another worker is running (lock held).")
        return
    try:
        while True:
            jobs = load_jobs()
            job = get_next_pending(jobs)
            if job is None:
                if loop:
                    time.sleep(poll_interval)
                    continue
                else:
                    print("No pending jobs. Exiting worker.")
                    return
            print(f"Running job {job['id']}: {' '.join(job.get('args', []))}")
            run_job(job, jobs)
    finally:
        if lock:
            try:
                lock.close()
            except Exception:
                pass


def parse_args(argv: List[str]) -> argparse.Namespace:
    if not argv:
        return argparse.Namespace(command="status", name=None, script_args=[])

    # Alias: a -> add
    argv = list(argv)
    if argv and argv[0] == "a":
        argv[0] = "add"

    parser = argparse.ArgumentParser(
        description="Simple queue runner for run.py",
        allow_abbrev=False,
        add_help=False,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add_p = sub.add_parser(
        "add",
        help="Add a job to the queue",
        allow_abbrev=False,
        add_help=False,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_p.add_argument("--name", help="Optional name/label for the job")

    sub.add_parser("status", help="Show job statuses", allow_abbrev=False, add_help=False, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    sub.add_parser("m", help="Open most recent frame", allow_abbrev=False, add_help=False, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    sub.add_parser("p", help="Play most recent mp4", allow_abbrev=False, add_help=False, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    sub.add_parser("o", help="Open output folder", allow_abbrev=False, add_help=False, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    l_p = sub.add_parser("l", help="Queue a job using the last run's settings", allow_abbrev=False, add_help=False, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    l_p.add_argument(
        "override_args",
        nargs=argparse.REMAINDER,
        help="Optional args to override last settings (e.g. -q high -n 50; use -- to stop parsing if needed)",
    )
    sub.add_parser("w", help="Watch latest run log (live)", allow_abbrev=False, add_help=False, formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    sub.add_parser("k", help="Kill all running workers and clear pending", allow_abbrev=False, add_help=False, formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    worker_p = sub.add_parser("worker", help=argparse.SUPPRESS, allow_abbrev=False, add_help=False, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    worker_p.add_argument("--loop", action="store_true", help=argparse.SUPPRESS)
    worker_p.add_argument("--poll-interval", type=int, default=10, help=argparse.SUPPRESS)

    if "-h" in argv or "--help" in argv:
        index_help = parser.format_help().rstrip()
        try:
            run_help = subprocess.run(
                [sys.executable, str(REPO_ROOT / "run.py"), "-h"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
        except Exception as exc:
            run_help = f"(Failed to load run.py help: {exc})"
        print(index_help)
        print("\nrun.py options:\n")
        print(run_help)
        raise SystemExit(0)

    cmd = argv[0]
    if cmd == "add":
        add_args, script_args = add_p.parse_known_args(argv[1:])
        return argparse.Namespace(command="add", name=add_args.name, script_args=script_args)
    if cmd == "l":
        # Treat everything after 'l' as override args (for run.py)
        return argparse.Namespace(command="l", override_args=argv[1:])

    return parser.parse_args(argv)


def main(argv: List[str]) -> None:
    # Fast-path for w to avoid argparse quirks in weird shells.
    if argv and argv[0] == "w":
        log_path = latest_log()
        if log_path is None:
            print(f"No logs found. Searched in {LOG_DIR} and {OUTPUT_ROOT}/output*/run.log")
            return
        watch_log(log_path)
        return

    args = parse_args(argv)
    if args.command == "add":
        if not args.script_args:
            raise SystemExit("No args provided for index.py. Example: python queue.py add -q final --seconds 60")
        job = add_job(args.script_args, args.name)
        print("Queued.")
        # Auto-start a detached worker (looping) to process the queue; lock prevents duplicates.
        try:
            subprocess.Popen(
                [
                    sys.executable,
                    str(REPO_ROOT / "index.py"),
                    "worker",
                    "--loop",
                    "--poll-interval",
                    "5",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f"Warning: failed to auto-start job: {exc}", file=sys.stderr)
    elif args.command == "status":
        print_status(load_jobs())
    elif args.command == "k":
        # Kill workers and running jobs (best-effort).
        killed_any = False
        for pattern, label in ((f"{Path(__file__).name} worker", "worker"), ("run.py", "job")):
            try:
                out = subprocess.check_output(["pgrep", "-f", pattern], text=True)
                pids = [int(pid) for pid in out.strip().splitlines() if pid.strip().isdigit()]
            except subprocess.CalledProcessError:
                pids = []
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                    print(f"Sent SIGTERM to {label} pid {pid}")
                    killed_any = True
                except Exception as exc:
                    print(f"Failed to kill pid {pid}: {exc}", file=sys.stderr)
        if not killed_any:
            print("No worker/job processes found.")
        # Drop everything from the queue (all statuses).
        jobs = load_jobs()
        total = len(jobs)
        save_jobs([])
        if total:
            print(f"Cleared {total} job(s) from queue.")
        else:
            print("Queue was already empty.")
    elif args.command == "m":
        target = OUTPUT_ROOT / "most_recent_frame.png"
        if not target.exists():
            print(f"Most recent frame not found at {target}")
            return
        try:
            opener = ["open"] if sys.platform == "darwin" else (["xdg-open"] if os.name == "posix" else ["start"])
            subprocess.Popen(opener + [str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"Opening {target}")
        except Exception as exc:
            print(f"Failed to open {target}: {exc}", file=sys.stderr)
    elif args.command == "p":
        target = OUTPUT_ROOT / "rendered_frames.mp4"
        if not target.exists():
            print(f"Video not found at {target}")
            return
        try:
            opener = ["open"] if sys.platform == "darwin" else (["xdg-open"] if os.name == "posix" else ["start"])
            subprocess.Popen(opener + [str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"Opening {target}")
        except Exception as exc:
            print(f"Failed to open {target}: {exc}", file=sys.stderr)
    elif args.command == "o":
        target = OUTPUT_ROOT
        if not target.exists():
            print(f"Output folder not found at {target}")
            return
        try:
            opener = ["open"] if sys.platform == "darwin" else (["xdg-open"] if os.name == "posix" else ["start"])
            subprocess.Popen(opener + [str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"Opening {target}")
        except Exception as exc:
            print(f"Failed to open {target}: {exc}", file=sys.stderr)
    elif args.command == "l":
        run_dir = latest_run_dir()
        if run_dir is None:
            print("No last run found.")
            return
        args_list: List[str] = []
        meta_path = run_dir / "run_metadata.json"
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except Exception:
                meta = {}
        if not meta:
            # Parse from run.log
            log_path = run_dir / "run.log"
            if log_path.exists():
                try:
                    for line in log_path.read_text().splitlines():
                        if "New run directory" in line and "quality=" in line and "duration=" in line and "bodies=" in line:
                            parts = line.split("quality=")[1]
                            quality_part, rest = parts.split(",", 1)
                            duration_part = rest.split("duration=")[1].split("s", 1)[0]
                            bodies_part = rest.split("bodies=")[1].split(")", 1)[0]
                            meta["quality"] = quality_part.strip()
                            meta["duration_seconds"] = float(duration_part)
                            meta["body_count"] = int(bodies_part)
                            break
                except Exception:
                    meta = {}
        if not meta:
            # Try config_used.json
            cfg_path = run_dir / "config_used.json"
            if cfg_path.exists():
                try:
                    cfg = json.loads(cfg_path.read_text())
                    meta["quality"] = cfg.get("default_quality")
                    meta["duration_seconds"] = cfg.get("duration_seconds")
                    meta["body_count"] = cfg.get("default_body_count")
                except Exception:
                    pass
        if meta.get("quality"):
            args_list += ["-q", str(meta["quality"])]
        if meta.get("body_count"):
            args_list += ["-n", str(meta["body_count"])]
        if meta.get("duration_seconds"):
            args_list += ["-t", str(meta["duration_seconds"])]
        if getattr(args, "override_args", None):
            args_list += args.override_args
        if not args_list:
            print("No usable options found in last run metadata/config.")
            return
        job = add_job(args_list, None)
        print("Queued.")
        try:
            subprocess.Popen(
                [
                    sys.executable,
                    str(REPO_ROOT / "index.py"),
                    "worker",
                    "--loop",
                    "--poll-interval",
                    "5",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f"Warning: failed to auto-start worker: {exc}", file=sys.stderr)
    elif args.command == "w":
        print("w hit: locating log...", flush=True)
        log_path = latest_log()
        if log_path is None:
            print(f"No logs found. Searched in {LOG_DIR} and {OUTPUT_ROOT}/output*/run.log")
            return
        print(f"w streaming: {log_path}", flush=True)
        watch_log(log_path)
    elif args.command == "worker":
        worker(loop=args.loop, poll_interval=args.poll_interval)
    else:
        raise SystemExit(f"Unknown command {args.command}")


if __name__ == "__main__":
    main(sys.argv[1:])
