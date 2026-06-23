"""FastAPI backend for the simulation/render pipeline frontend.

Phase 0: read-only gallery. Serves the list of runs and their artifacts
(frames, thumbnail, video) straight off the output/ directory.

Run with:
    uvicorn app:app --reload --port 8000
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Any, Dict, Optional

import runs
import jobmanager
import presets
from config import DEFAULT_CONFIG, FIELD_SCHEMA, CAMERA_MOVE_SCHEMA, SCENARIO_CHOICES

app = FastAPI(title="Sim/Render Pipeline API", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    jobmanager.load_persisted()

# Dev convenience: the Vite dev server runs on a different port. In production
# (single host) the frontend is served same-origin and this is a no-op.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "output_root": str(runs.OUTPUT_ROOT)}


def _healthz_detail(job: Dict[str, Any], n: int) -> str:
    """Human-readable summary of what's currently rendering, for Shelf's tooltip."""
    label = job.get("name") or f"job-{job['id']}"
    bits = [f"rendering {label}"]
    run_dir = job.get("run_dir")
    if run_dir:
        try:
            done = sum(1 for _ in (Path(run_dir) / "rendered_frames").glob("*.png"))
            if done:
                bits.append(f"{done} frames")
        except OSError:
            pass
    if n > 1:
        bits.append(f"(+{n - 1} more)")
    return ", ".join(bits)


@app.get("/healthz")
def healthz() -> JSONResponse:
    """Liveness probe for Shelf: green while a sim/render is working, gray when not.

    Shelf treats a 2xx as "up". We return 200 + status "active" only while a job
    is running or queued; when idle we return 503 so a Shelf health spec with
    ``expect_up: false`` paints it *inactive* (gray) — the same gray as the
    backend being off entirely. So: green = something is rendering, gray = idle
    or off.
    """
    active = jobmanager.active_jobs()
    if not active:
        return JSONResponse(
            {"status": "idle", "detail": "no active render", "active": 0},
            status_code=503,
        )
    return JSONResponse(
        {
            "status": "active",
            "detail": _healthz_detail(active[0], len(active)),
            "active": len(active),
            "jobs": [j["id"] for j in active],
        },
        status_code=200,
    )


@app.get("/api/config/defaults")
def config_defaults() -> dict:
    """Resolved default config — used to seed the job-builder forms."""
    return DEFAULT_CONFIG


@app.get("/api/config/fields")
def config_fields() -> dict:
    """Schema of editable tunables — the builder renders its form from this, so
    config keys/labels live in one place (config.py) instead of the frontend."""
    return {"fields": FIELD_SCHEMA, "camera_move": CAMERA_MOVE_SCHEMA, "scenarios": SCENARIO_CHOICES}


# ---- Jobs -----------------------------------------------------------------

class JobRequest(BaseModel):
    quality: Optional[str] = None
    num_bodies: Optional[int] = None
    seconds: Optional[float] = None
    first_frame: bool = False
    config_override: Optional[Dict[str, Any]] = None
    name: Optional[str] = None
    prep_scene: bool = False              # -p: build an editable .blend, skip render
    resume_run_id: Optional[int] = None   # -r: render into an existing run
    physics_from_run_id: Optional[int] = None  # -ph: reuse a run's physics, fresh render
    blender_scene: Optional[str] = None   # -b: render from a .blend


@app.post("/api/jobs")
def create_job(req: JobRequest) -> dict:
    return jobmanager.create_job(req.model_dump())


@app.get("/api/jobs")
def list_jobs() -> list:
    return jobmanager.list_jobs()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int) -> dict:
    job = jobmanager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return job


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int) -> dict:
    try:
        return jobmanager.cancel_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int) -> dict:
    """Delete a job and its source files (logs, override, output run dir)."""
    try:
        jobmanager.delete_job(job_id)
        return {"deleted": job_id}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/api/jobs/{job_id}/logs")
def job_logs(job_id: int):
    if jobmanager.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return StreamingResponse(
        jobmanager.tail_log(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- Presets --------------------------------------------------------------

class PresetRequest(BaseModel):
    name: str
    quality: Optional[str] = None
    num_bodies: Optional[int] = None
    seconds: Optional[float] = None
    first_frame: bool = False
    config_override: Optional[Dict[str, Any]] = None


@app.get("/api/presets")
def get_presets() -> list:
    return presets.list_presets()


@app.post("/api/presets")
def save_preset(req: PresetRequest) -> dict:
    return presets.save_preset(req.model_dump())


@app.delete("/api/presets/{name}")
def delete_preset(name: str) -> dict:
    if not presets.delete_preset(name):
        raise HTTPException(status_code=404, detail=f"preset {name} not found")
    return {"deleted": name}


# ---- Blender (native scene editing) ---------------------------------------

def _resolve_blender() -> Optional[str]:
    for cand in DEFAULT_CONFIG.get("blender_candidates", []):
        if not cand:
            continue
        p = Path(cand)
        if p.is_absolute() and p.exists():
            return str(p)
        # relative to project root, or on PATH
        rel = runs.OUTPUT_ROOT.parent / cand
        if rel.exists():
            return str(rel)
        found = shutil.which(cand)
        if found:
            return found
    return None


class OpenSceneRequest(BaseModel):
    run_id: int


@app.post("/api/blender/open")
def open_scene(req: OpenSceneRequest) -> dict:
    """Launch the Blender GUI on a run's editable scene (local desktop only)."""
    scene = runs.run_dir_for(req.run_id) / "scene_edit.blend"
    if not scene.exists():
        raise HTTPException(status_code=404, detail="scene_edit.blend not found for this run")
    blender = _resolve_blender()
    if not blender:
        raise HTTPException(status_code=500, detail="Blender binary not found")
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        raise HTTPException(status_code=409, detail="No display available to open Blender (headless host)")
    try:
        subprocess.Popen(
            [blender, str(scene)],
            cwd=str(runs.OUTPUT_ROOT.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to launch Blender: {exc}")
    return {"opened": str(scene)}


class OpenFolderRequest(BaseModel):
    run_id: Optional[int] = None   # open a specific run dir; omit for the output root


@app.post("/api/open-folder")
def open_folder(req: OpenFolderRequest) -> dict:
    """Open the output folder (or one run's dir) in the host's file manager.

    Local-desktop action: opens on the *host* (belphegor), not the remote
    browser's machine. Needs the host's graphical session, which the systemd
    user service inherits (DISPLAY/XAUTHORITY/DBUS).
    """
    target = runs.run_dir_for(req.run_id) if req.run_id is not None else runs.OUTPUT_ROOT
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"folder not found: {target}")
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        raise HTTPException(status_code=409, detail="No display available (headless host)")
    try:
        subprocess.Popen(
            ["xdg-open", str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to open folder: {exc}")
    return {"opened": str(target)}


@app.get("/api/runs")
def get_runs() -> list:
    return runs.list_runs()


@app.get("/api/runs/{run_id}")
def get_run(run_id: int) -> dict:
    detail = runs.run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return detail


@app.get("/api/runs/{run_id}/spec")
def get_run_spec(run_id: int) -> dict:
    """Settings derived from a run, to seed a new job in the builder."""
    spec = runs.run_spec(run_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return spec


@app.get("/api/runs/{run_id}/frames/{index}")
def get_frame(run_id: int, index: int):
    run_dir = runs.run_dir_for(run_id)
    path = runs.frame_path(run_dir, index)
    if path is None:
        raise HTTPException(status_code=404, detail="frame not found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/runs/{run_id}/thumb")
def get_thumb(run_id: int):
    """First rendered frame, used as a gallery thumbnail."""
    run_dir = runs.run_dir_for(run_id)
    indices = runs.list_frame_indices(run_dir)
    if not indices:
        raise HTTPException(status_code=404, detail="no frames")
    path = runs.frame_path(run_dir, indices[0])
    if path is None:
        raise HTTPException(status_code=404, detail="no frames")
    return FileResponse(path, media_type="image/png")


@app.get("/api/runs/{run_id}/video")
def get_video(run_id: int):
    run_dir = runs.run_dir_for(run_id)
    path = runs.video_path(run_dir)
    if path is None:
        raise HTTPException(status_code=404, detail="no video")
    # FileResponse honours Range requests, so the <video> element can seek.
    return FileResponse(path, media_type="video/mp4")


# ---- Static frontend (always-on serve) ------------------------------------
# In dev the Vite server on :5173 proxies /api here. For the boot-persistent
# Tailscale deploy we serve the *built* SPA same-origin, so it's one process on
# one port. Mounted LAST so it never shadows the /api routes above; html=True
# serves index.html at "/" and 404s fall back to it for the single-page app.
_FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
