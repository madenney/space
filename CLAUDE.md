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
python3 index.py k                           # Kill workers
```

## Architecture

**Pipeline flow:** `run.py` → `physics.py` (Chrono simulation) → `blender_driver.py` (Alembic export + Cycles render)

### Core Components

- **run.py**: Main orchestrator. Creates isolated numbered output directories, handles CLI args, coordinates simulation and rendering
- **physics.py**: Runs pychrono physics simulation, spawns random bodies (boxes/spheres/cylinders), exports motion data as NPZ
- **blender_driver.py**: Generates `blender_stage.py` script dynamically, handles Alembic export and Cycles rendering, manages GPU device detection
- **config.py**: Configuration management with quality presets (low/high/final) and deep merge logic
- **index.py**: Job queue system with file locking, supports add/status/worker/watch commands
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
