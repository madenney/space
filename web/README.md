# Sim / Render Studio — web frontend

A local web UI for the physics/render pipeline. Built in phases:

- **Phase 0 (done):** read-only gallery — browse `output/outputN/` runs, scrub
  frames, play the MP4, inspect config/metadata.
- Phase 1: job builder + full render + live logs
- Phase 2: lighting-iteration loop (data-driven camera/lights, first-frame preview)
- Phase 3: polish (presets, thumbnails)

## Architecture

```
web/
  backend/   FastAPI — reads output/ as the source of truth (no DB)
  frontend/  React + Vite
```

The backend reuses the pipeline's `config.py` for `OUTPUT_ROOT`, so it always
points at the same `output/` directory `run.py` writes to. All artifacts are
served over HTTP (not local file opens), so the eventual remote deployment is a
proxy change, not a code change.

## Run it (two terminals)

Backend:
```bash
cd web/backend
.venv/bin/uvicorn app:app --reload --port 8000
```

Frontend:
```bash
cd web/frontend
npm run dev      # http://localhost:5173
```

Vite proxies `/api/*` to the backend on :8000.

## First-time setup

```bash
# backend
cd web/backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# frontend
cd web/frontend && npm install
```
