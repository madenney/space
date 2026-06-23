#!/usr/bin/env python3
"""Terminal client for the Sim/Render studio.

This is a thin CLI over the web backend's job queue — it does NOT run its own
queue or worker. Every command talks to the always-on service so there is a
SINGLE place that serializes the GPU (the studio's worker). Point it elsewhere
with $SPACE_API (default http://127.0.0.1:8780).

Examples:
  python index.py add -q final -t 60 -n 300   # queue a render
  python index.py a -q low -t 4               # 'a' is short for add
  python index.py                             # status (default)
  python index.py w                           # follow the active job's log
  python index.py l -q high                   # re-run the last job, tweaked
  python index.py k                           # cancel running + pending
  python index.py m | p | o                   # open last frame | video | folder
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = REPO_ROOT / "output"
API = os.environ.get("SPACE_API", "http://127.0.0.1:8780").rstrip("/")


# ---- HTTP to the backend ---------------------------------------------------

def _die_unreachable(exc: Exception) -> None:
    print(
        f"Can't reach the studio backend at {API}.\n"
        f"Is it running?   systemctl --user status space-web\n"
        f"Or set $SPACE_API to where it lives.\n({exc})",
        file=sys.stderr,
    )
    raise SystemExit(2)


def _request(method: str, path: str, body: Optional[dict] = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        print(f"{method} {path} -> {exc.code}: {detail}", file=sys.stderr)
        raise SystemExit(1)
    except urllib.error.URLError as exc:
        _die_unreachable(exc)


def api_get(path: str) -> Any:
    return _request("GET", path)


def api_post(path: str, body: dict) -> Any:
    return _request("POST", path, body)


# ---- local helpers (open files on this machine) ----------------------------

def _open(path: Path) -> None:
    if not path.exists():
        print(f"Not found: {path}")
        return
    opener = ["open"] if sys.platform == "darwin" else ["xdg-open"]
    try:
        subprocess.Popen(opener + [str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"Opening {path}")
    except Exception as exc:
        print(f"Failed to open {path}: {exc}", file=sys.stderr)


def _latest_run_dir() -> Optional[Path]:
    if not OUTPUT_ROOT.exists():
        return None
    dirs = []
    for child in OUTPUT_ROOT.iterdir():
        if child.is_dir() and child.name.startswith("output") and child.name[6:].isdigit():
            dirs.append((int(child.name[6:]), child))
    return max(dirs, key=lambda x: x[0])[1] if dirs else None


# ---- request building (run.py-style flags -> JobRequest) -------------------

def _add_parser(prog: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, add_help=False)
    p.add_argument("-q", "--quality")
    p.add_argument("-n", "--num-bodies", type=int)
    p.add_argument("-t", "--seconds", type=float)
    p.add_argument("-f", "--first-frame", action="store_true")
    p.add_argument("-p", "--prep-scene", action="store_true")
    p.add_argument("-r", "--resume", type=int, metavar="N", help="resume render of output N")
    p.add_argument("-ph", "--physics-from", type=int, metavar="N", help="reuse physics from output N")
    p.add_argument("-b", "--blender-scene")
    p.add_argument("-c", "--config", help="JSON file merged as a config override")
    p.add_argument("--name")
    return p


def _request_from_args(ns: argparse.Namespace) -> Dict[str, Any]:
    req: Dict[str, Any] = {}
    if ns.quality:
        req["quality"] = ns.quality
    if ns.num_bodies is not None:
        req["num_bodies"] = ns.num_bodies
    if ns.seconds is not None:
        req["seconds"] = ns.seconds
    if ns.first_frame:
        req["first_frame"] = True
    if ns.prep_scene:
        req["prep_scene"] = True
    if ns.resume is not None:
        req["resume_run_id"] = ns.resume
    if ns.physics_from is not None:
        req["physics_from_run_id"] = ns.physics_from
    if ns.blender_scene:
        req["blender_scene"] = ns.blender_scene
    if ns.name:
        req["name"] = ns.name
    if ns.config:
        cfg_path = Path(ns.config)
        if not cfg_path.exists():
            print(f"Config file not found: {cfg_path}", file=sys.stderr)
            raise SystemExit(1)
        req["config_override"] = json.loads(cfg_path.read_text())
    return req


# ---- commands --------------------------------------------------------------

def cmd_add(argv: List[str]) -> None:
    ns = _add_parser("index.py add").parse_args(argv)
    req = _request_from_args(ns)
    if not req:
        print("Nothing to queue. Example: index.py add -q final -t 60 -n 300", file=sys.stderr)
        raise SystemExit(1)
    job = api_post("/api/jobs", req)
    print(f"Queued #{job['id']} ({job.get('status')}): run.py {' '.join(job.get('args', []))}")


def cmd_status(_argv: List[str]) -> None:
    jobs = api_get("/api/jobs") or []
    if not jobs:
        print("No jobs.")
        return
    hdr = f"{'ID':>3}  {'Status':<10}  {'Name':<22}  {'Output':<10}  Args"
    print(hdr)
    print("-" * len(hdr))
    for j in sorted(jobs, key=lambda x: x.get("id", 0)):
        out = f"output{j['run_id']}" if j.get("run_id") is not None else ""
        print(f"{j.get('id', 0):>3}  {j.get('status', '?'):<10}  "
              f"{(j.get('name') or ''):<22}  {out:<10}  {' '.join(j.get('args', []))}")


def cmd_watch(_argv: List[str]) -> None:
    jobs = api_get("/api/jobs") or []
    if not jobs:
        print("No jobs to watch.")
        return
    active = next((j for j in jobs if j.get("status") in ("running", "pending")), None)
    job = active or jobs[0]  # jobs come newest-first
    print(f"Following #{job['id']} ({job.get('status')}) — Ctrl-C to stop", flush=True)
    try:
        with urllib.request.urlopen(f"{API}/api/jobs/{job['id']}/logs") as resp:
            for raw in resp:
                line = raw.decode(errors="replace").rstrip("\n")
                if line.startswith("event: done"):
                    print("[done]", flush=True)
                    return
                if line.startswith("data:"):
                    print(line[5:].lstrip(), flush=True)
    except KeyboardInterrupt:
        pass
    except urllib.error.URLError as exc:
        _die_unreachable(exc)


def cmd_last(argv: List[str]) -> None:
    """Re-queue the most recent job, with optional overriding flags."""
    jobs = api_get("/api/jobs") or []
    if not jobs:
        print("No previous job to re-run.")
        return
    base = dict(jobs[0].get("request") or {})  # newest-first
    base.pop("name", None)
    if argv:
        overrides = _request_from_args(_add_parser("index.py l").parse_args(argv))
        base.update(overrides)
    job = api_post("/api/jobs", base)
    print(f"Queued #{job['id']}: run.py {' '.join(job.get('args', []))}")


def cmd_kill(_argv: List[str]) -> None:
    jobs = api_get("/api/jobs") or []
    active = [j for j in jobs if j.get("status") in ("running", "pending")]
    if not active:
        print("Nothing running or pending.")
        return
    for j in active:
        api_post(f"/api/jobs/{j['id']}/cancel", {})
        print(f"Cancelled #{j['id']}")


def cmd_frame(_argv: List[str]) -> None:
    _open(OUTPUT_ROOT / "most_recent_frame.png")


def cmd_video(_argv: List[str]) -> None:
    run_dir = _latest_run_dir()
    if run_dir is None:
        print("No runs yet.")
        return
    _open(run_dir / "rendered_frames.mp4")


def cmd_folder(_argv: List[str]) -> None:
    _open(OUTPUT_ROOT)


COMMANDS = {
    "add": cmd_add, "a": cmd_add,
    "status": cmd_status,
    "w": cmd_watch,
    "l": cmd_last,
    "k": cmd_kill,
    "m": cmd_frame,
    "p": cmd_video,
    "o": cmd_folder,
}


def main(argv: List[str]) -> None:
    if not argv:
        cmd_status([])
        return
    if argv[0] in ("-h", "--help"):
        print(__doc__)
        print(f"\nbackend: {API}\ncommands: add(a) status w l k m p o")
        return
    cmd = argv[0]
    fn = COMMANDS.get(cmd)
    if fn is None:
        print(f"Unknown command: {cmd}\nTry: add status w l k m p o (or -h)", file=sys.stderr)
        raise SystemExit(2)
    fn(argv[1:])


if __name__ == "__main__":
    main(sys.argv[1:])
