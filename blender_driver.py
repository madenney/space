import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple


# The render stage is now a static, committed file (blender_stage.py) instead of
# a per-run generated f-string. It reads a run's config_used.json / metadata /
# NPZ at runtime and takes a few flags after `--`. See blender_stage.py.
STAGE_SCRIPT = Path(__file__).resolve().parent / "blender_stage.py"


def find_blender(logger, config) -> str:
    for candidate in config["blender_candidates"]:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            logger.info("Using Blender binary at %s", path)
            return str(path)
        resolved = shutil.which(str(candidate))
        if resolved:
            logger.info("Using Blender binary at %s", resolved)
            return resolved
    logger.error("Blender binary not found. Set BLENDER_BIN or add Blender to PATH.")
    sys.exit(1)


def prepare_blender_stage(
    run_dir: Path,
    quality: str,
    first_frame_only: bool,
    resume_frame: int = 0,
    config: Optional[dict] = None,
    scene_path: Optional[Path] = None,
    scene_output_path: Optional[Path] = None,
    stop_after_scene: Optional[bool] = None,
    preserve_materials: Optional[bool] = None,
) -> Tuple[Path, List[str]]:
    """Copy the static stage script into the run dir (for provenance + standalone
    debugging) and build the argv that parameterizes it.

    Everything else the stage needs — camera, lights, world, samples, fps,
    bodies — it reads from the run's own config_used.json / run_metadata.json, so
    only the per-invocation knobs are passed as flags here.
    """
    cfg_data = config or {}
    presets = cfg_data.get("quality_presets", {})
    if quality not in presets and "high" not in presets:
        raise ValueError(f"Quality preset '{quality}' not found in config.")

    # Keep a copy alongside the run so the directory stays self-describing and the
    # stage can be re-run by hand: blender -P <run>/blender_stage.py -- --run-dir <run>
    script_dst = run_dir / "blender_stage.py"
    shutil.copyfile(STAGE_SCRIPT, script_dst)

    args: List[str] = ["--run-dir", str(run_dir), "--quality", str(quality)]
    if resume_frame:
        args += ["--resume-frame", str(int(resume_frame))]
    if first_frame_only:
        args.append("--first-frame")

    stop_val = stop_after_scene if stop_after_scene is not None else bool(
        cfg_data.get("blender_stop_after_scene", False)
    )
    if stop_val:
        args.append("--stop-after-scene")

    preserve_val = preserve_materials if preserve_materials is not None else bool(
        cfg_data.get("blender_preserve_materials", False)
    )
    if preserve_val:
        args.append("--preserve-materials")

    scene_p = str(scene_path) if scene_path is not None else (cfg_data.get("blender_scene") or "")
    if scene_p:
        args += ["--scene-path", scene_p]

    scene_o = (
        str(scene_output_path)
        if scene_output_path is not None
        else (cfg_data.get("blender_scene_output") or "")
    )
    if scene_o:
        args += ["--scene-output", scene_o]

    return script_dst, args


def run_blender(
    blender_bin: str,
    script_path: Path,
    run_dir: Path,
    logger,
    stage_args: Optional[List[str]] = None,
) -> None:
    log_path = run_dir / "blender.log"
    # --python-exit-code makes Blender propagate Python script errors to its
    # exit status (otherwise it exits 0 even when the -P script raises).
    cmd = [blender_bin, "-b", "--python-exit-code", "1", "-P", str(script_path)]
    if stage_args:
        cmd += ["--"] + list(stage_args)
    logger.info("-> %s", " ".join(map(str, cmd)))
    with log_path.open("w") as log_file:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        assert proc.stdout is not None
        progress_prefixes = ("Keyframed ", "Rendering frames:")
        last_progress_len = 0
        for line in proc.stdout:
            log_file.write(line)
            log_file.flush()
            # Filter Blender verbosity for console; keep our own prints like "Rendering frames X/Y"
            stripped = line.strip()
            noisy = (
                stripped.startswith("Fra:")
                or stripped.startswith("Saved:")
                or "ViewLayer |" in stripped
                or stripped.startswith("Time:")
            )
            if noisy or not stripped:
                continue
            if any(stripped.startswith(pfx) for pfx in progress_prefixes):
                pad = max(0, last_progress_len - len(stripped))
                sys.stdout.write("\r" + stripped + (" " * pad))
                sys.stdout.flush()
                last_progress_len = len(stripped)
            else:
                if last_progress_len:
                    sys.stdout.write("\n")
                    last_progress_len = 0
                sys.stdout.write(stripped + "\n")
        proc.wait()
        if last_progress_len:
            sys.stdout.write("\n")
        if proc.returncode != 0:
            logger.error("Blender failed, see %s", log_path)
            sys.exit(proc.returncode)
    logger.info("Blender completed. Log written to %s", log_path)
