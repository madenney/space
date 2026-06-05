"""FastAPI backend for the simulation/render pipeline frontend.

Phase 0: read-only gallery. Serves the list of runs and their artifacts
(frames, thumbnail, video) straight off the output/ directory.

Run with:
    uvicorn app:app --reload --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import Any, Dict, Optional

import runs
import jobmanager
from config import DEFAULT_CONFIG

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


@app.get("/api/config/defaults")
def config_defaults() -> dict:
    """Resolved default config — used to seed the job-builder forms."""
    return DEFAULT_CONFIG


# ---- Jobs -----------------------------------------------------------------

class JobRequest(BaseModel):
    quality: Optional[str] = None
    num_bodies: Optional[int] = None
    seconds: Optional[float] = None
    first_frame: bool = False
    config_override: Optional[Dict[str, Any]] = None
    name: Optional[str] = None


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


@app.get("/api/jobs/{job_id}/logs")
def job_logs(job_id: int):
    if jobmanager.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return StreamingResponse(
        jobmanager.tail_log(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/runs")
def get_runs() -> list:
    return runs.list_runs()


@app.get("/api/runs/{run_id}")
def get_run(run_id: int) -> dict:
    detail = runs.run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return detail


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
