# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Physics simulation and 3D rendering pipeline that runs simulations using Project Chrono, exports to Alembic format, and renders with Blender's Cycles engine.

## Commands

```bash
# Activate environment (required before running)
conda activate chrono

# Run full pipeline with defaults
python3 run.py

# Run with custom parameters
python3 run.py -q high -n 50 -t 5   # quality preset, body count, duration

# Resume interrupted run
python3 run.py -r output/output5

# Reuse physics data with different render settings
python3 run.py -ph output/output3 -q final

# Prepare editable scene (exports .blend file)
python3 run.py -p
# After editing: python3 run.py -r outputN -b scene_edit.blend

# Stitch existing frames into MP4
python3 run.py -s output/output5

# Job queue operations
python3 index.py add -q final --seconds 10  # Queue job
python3 index.py w                           # Watch live logs
python3 index.py m                           # Open most recent frame
python3 index.py k                           # Cancel running + pending jobs
```

## Architecture

**Pipeline flow:** `run.py` → `physics.py` (Chrono simulation) → `blender_driver.py` (Alembic export + Cycles render)

### Core Components

- **run.py**: Main orchestrator. Creates isolated numbered output directories, handles CLI args, picks the scenario, coordinates simulation and rendering
- **motion.py**: The motion contract — the single NPZ format both sides agree on. Structure-of-arrays (`positions (F,N,3)`, optional `orientations`, `time`, `frame_index`, optional `ch_<name>` channels), float32, flat/vectorizable. `write_motion()`/`read_motion()`. Extensible: a heat sim just adds a channel. (Legacy runs use an older per-body layout, still readable.)
- **scenarios.py**: Registry of pluggable sims, each `fn(run_dir, logger, duration, fps, hz, config) -> {npz_path, metadata}`. The scenarios differ only in COLLISIONS: `gravity` = none (point particles pass through); `collide` = soft-sphere DEM (penalty spring+dashpot, NumPy); `rigid` = Chrono hard contact (constraint solver, zero overlap, in physics.py). Selected by `config["scenario"]`
- **gravity.py**: The shared N-body gravity kernel — the ONE softened force law (`gravity_accel`) and exact↔Barnes-Hut choice (`use_tree`/`describe`) that all three scenarios call. Gravity is what they share; collisions are what they don't. (Barnes-Hut tree itself lives in `barnes_hut.py`.)
- **physics.py**: The `rigid` scenario — pychrono rigid-body sim, spawns random bodies (boxes/spheres/cylinders), long-range gravity from the shared `gravity.py` kernel, hard contacts from Chrono's solver. Exports the motion contract (positions + orientations)
- **blender_driver.py**: Locates Blender, copies the static `blender_stage.py` into the run dir, and launches it headless with per-run flags (`-- --run-dir … --quality … [--resume-frame N]`); streams/filters Blender's log
- **blender_stage.py**: Static (NOT generated) Blender script run inside Blender. Reads the run's own config_used.json / run_metadata.json / NPZ, builds animated meshes, exports+imports Alembic, assembles the scene (materials/world/camera/lights), and renders. Camera is a first-class spec here (`resolve_camera()`: static/track/orbit/keyframes + look_at origin/clump/point)
- **config.py**: Configuration management with quality presets (low/high/final) and deep merge logic
- **index.py**: Thin terminal client over the web backend's job queue (POSTs to the FastAPI service at $SPACE_API, default :8780). No queue/worker of its own — CLI and web share ONE GPU serialization point. Commands: add/a, status, w (follow log), l (re-run last), k (cancel active), m/p/o (open frame/video/folder)
- **logger.py**: Unified logging to console and file

### Output Structure

```
output/outputN/
├── run.log, blender.log      # Logs
├── config_used.json          # Resolved config for reproducibility
├── physics/motion_data.npz   # Chrono poses (NumPy)
├── alembic/motion_data.abc   # Alembic cache
└── rendered_frames/          # PNGs + final MP4
```

### Key Patterns

- **Coordinate transform**: Chrono Y-up → Blender Z-up (handled in blender_driver.py)
- **GPU detection order**: OptiX > CUDA > HIP > Metal > CPU fallback
- **Config inheritance**: JSON configs deeply merge with defaults
- **Motion data format**: NPZ arrays with shape (frames, [frame_idx, time, x, y, z, qw, qx, qy, qz])

## Dependencies

- **pychrono**: Physics engine (via conda chrono environment)
- **Blender**: Bundled 4.2.0 in `blender-4.2.0-linux-x64/` or override with `BLENDER_BIN` env var
- **ffmpeg**: Optional, for MP4 encoding
