## Pipeline overview
- One command runs the full pipeline: `conda activate chrono` then `python3 run.py`.
- The script simulates in Project Chrono, exports pose data for multiple bodies, feeds Blender headless to rebuild the animation, writes an Alembic cache, and renders PNG frames (and an MP4 if ffmpeg is present).
- Each run is isolated in a new numbered folder inside `output/` (`output1`, `output2`, …) so runs do not overwrite each other.
- You can resume rendering an existing run with `python3 run.py -r output/outputN` or stitch only the already-rendered frames into an MP4 with `python3 run.py -s output/outputN`.

## What happens in `run.py`
1) **Run folder**: Creates `output/outputN/` (incrementing the highest existing). Saves the resolved config to `config_used.json` and sets up logging to `run.log`.
2) **Chrono sim**:
   - Initializes gravity (Y-up) and a ground plane.
   - Spawns multiple bodies (boxes/spheres/cylinders, random sizes, colors, and random linear/angular velocities; count controlled by `BODY_COUNT` env var).
   - Steps 2 seconds at 60 FPS (120 frames), storing pose per frame.
   - Saves `frame_data/motion_data.npz` (per-body arrays) and run metadata (`run_metadata.json`). Logs first few frames to `run.log`.
3) **Blender driver generation**:
   - Writes `blender_stage.py` into the run folder. This script remaps Chrono Y-up data to Blender Z-up (rotate +90° about X), rebuilds all bodies with matching shapes/sizes/colors, adds ground, camera, sun, sky/HDRI.
   - Exports `frame_data/motion_data.abc` (Alembic) for inspection.
   - Renders PNG frames with Cycles to `rendered_frames/`. Tries GPU (OptiX/CUDA/HIP/Metal), otherwise falls back to CPU. Res/samples/denoise/motion blur depend on quality preset.
   - Emits a console progress counter during rendering (e.g., `Rendering 4/120 frames`).
4) **Blender invocation**: Finds Blender (`BLENDER_BIN` env var, then bundled `blender-4.2.0-linux-x64/blender`, then `blender-3.2.0-linux-x64/blender`, then system `blender`) and runs it headless. Blender output is captured in `blender.log`.
5) **Video**: If `ffmpeg` is on PATH, stitches `rendered_frames/frame_####.png` into `rendered_frames.mp4` at the sim frame rate.

## Output structure (per run)
```
output/outputN/
├── blender.log          # stdout/stderr from headless Blender
├── blender_stage.py     # auto-generated driver script
├── run.log              # main pipeline log
├── run_metadata.json    # seed, frame count, fps, duration
├── frame_data/
│   ├── motion_data.npz  # Chrono poses (per body)
│   └── motion_data.abc  # Alembic exported by Blender
└── rendered_frames/
    ├── frame_####.png   # rendered images
    └── rendered_frames.mp4  # stitched video (if ffmpeg available)

Note: older runs used a `frame_images/` folder; the current pipeline does not generate that folder.
```

## Config knobs
- Defaults live in `config.py` (`DEFAULT_CONFIG`). Per-run overrides: `python run.py -c my_config.json`.
- Resolved config is saved per run to `config_used.json` in the output folder for audit/reuse.
- `SIM_SEED`: set for deterministic initial velocities.
- `BLENDER_BIN`: override Blender binary path.
- `BODY_COUNT`: how many bodies to spawn (default comes from config; CLI `-n` overrides).
- `HDRI_PATH`: optional path to an HDRI image for lighting instead of procedural sky.
- `blender_scene`: optional path to a .blend file to load for the render stage (lets you author camera/lights/sets in Blender).
- `blender_scene_use_camera`, `blender_scene_use_lights`, `blender_scene_use_world`: when `blender_scene` is set, keep the scene's camera/lights/world if present; set to `false` to force defaults.
- `blender_environment`: optional path to a .blend file whose first world + collection are appended on top of the generated scene.
- `blender_scene_output`: optional .blend path to save after Alembic import (relative paths are under the run directory).
- `blender_stop_after_scene`: if true, save the .blend and exit before rendering.
- `blender_preserve_materials`: if true, skip auto material assignment (useful when editing materials in Blender).
- `-q/--quality`: render preset (defaults to config’s `default_quality`; presets defined in config).

## Custom Blender scene
1) Open Blender, build your environment (camera/lights/props), and save a `.blend` file.
2) Create a config override:
```
{
  "blender_scene": "scenes/my_scene.blend",
  "blender_scene_use_camera": true,
  "blender_scene_use_lights": true,
  "blender_scene_use_world": true
}
```
3) Run: `python3 run.py -c my_scene.json`.

## Editable Blender scene workflow
1) Prepare a run and export an editable scene:
   - `python3 run.py -p` (or `--prep-scene`, `--scene-out path/to/edit.blend`)
2) Open the saved .blend in Blender, edit, and save.
3) Render using the edited scene:
   - `python3 run.py -r output/outputN -b output/outputN/scene_edit.blend`
   - Prepped scenes skip Alembic re-import/material overrides to preserve edits.

## Alembic?
`motion_data.abc` is an Alembic cache exported by Blender so you can inspect or ingest the animated geometry in other DCCs/tools without rerunning the sim. It contains the animated transforms for all bodies reconstructed from the Chrono data.

## Notes
- Axis remap is applied (Chrono Y-up → Blender Z-up) for both position and quaternion.
- Legacy/old helper scripts and logs live in `old/`; the pipeline now runs entirely from `run.py`.
- Visual tweaks: sky lighting, warmer rock with noise/bump, muted ground, Filmic color management.
