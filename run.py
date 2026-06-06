#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
import io
import numpy as np
from pathlib import Path

from blender_driver import find_blender, run_blender, write_blender_driver
from config import load_config, save_config, DEFAULT_CONFIG
from logger import setup_logger
from physics import run_chrono_sim


def next_run_dir(base: Path) -> Path:
    base.mkdir(exist_ok=True)
    highest = 0
    for child in base.iterdir():
        if child.is_dir() and child.name.startswith("output"):
            suffix = child.name[len("output") :]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    run_dir = base / f"output{highest + 1}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def run_ffmpeg(run_dir: Path, fps: int, logger, config: dict) -> None:
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        logger.warning("ffmpeg not found in PATH; skipping mp4 generation.")
        return
    input_pattern = run_dir / "rendered_frames" / "frame_%04d.png"
    if not any(run_dir.joinpath("rendered_frames").glob("frame_*.png")):
        logger.warning("No rendered frames found; skipping mp4 generation.")
        return
    mp4_path = run_dir / "rendered_frames.mp4"
    cmd = [
        ffmpeg_bin,
        "-y",
        "-framerate",
        str(fps),
        "-i",
        str(input_pattern),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(mp4_path),
    ]
    logger.info("-> %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("ffmpeg failed: %s", result.stderr.strip())
        return
    logger.info("Video written to %s", mp4_path)
    dest = Path(config["output_root"]).expanduser() / "rendered_frames.mp4"
    shutil.copyfile(mp4_path, dest)
    logger.info("Copied final video to %s (overwrite)", dest)


def load_existing_run(run_dir: Path, logger) -> dict:
    npz_path = run_dir / "physics" / "motion_data.npz"
    meta_path = run_dir / "run_metadata.json"
    if not npz_path.exists():
        logger.error("Resume requested but missing physics data at %s", npz_path)
        sys.exit(1)
    metadata = {}
    if meta_path.exists():
        try:
            metadata = json.loads(meta_path.read_text())
        except Exception:
            logger.warning("Failed to read run_metadata.json; attempting to reconstruct.")
    else:
        logger.warning("Missing run_metadata.json; attempting to reconstruct.")
    needs_reconstruct = not metadata
    if metadata and "bodies" not in metadata:
        logger.warning("run_metadata.json missing 'bodies'; inferring frame counts from NPZ.")
        needs_reconstruct = True
    # Reconstruct minimal metadata from npz if needed
    if needs_reconstruct:
        try:
            arr = np.load(npz_path)
            first_key = arr.files[0]
            frames = arr[first_key].shape[0]
            body_count = len(arr.files)
        except Exception:
            frames = 0
            body_count = 0
        reconstructed = {
            "frames": frames,
            "frame_rate": None,
            "duration_seconds": None,
            "bodies": [],
            "body_count": body_count,
        }
        reconstructed.update(metadata)
        reconstructed["frames"] = frames
        reconstructed["body_count"] = body_count
        if reconstructed.get("duration_seconds") is None and reconstructed.get("frame_rate"):
            reconstructed["duration_seconds"] = frames / reconstructed["frame_rate"]
        metadata = reconstructed
    logger.info("Using existing physics data: %s", npz_path)
    return {"npz_path": npz_path, "metadata": metadata}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Chrono -> Alembic -> Blender render pipeline",
        add_help=False,
        allow_abbrev=False,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-h", "--help", action="help", help=argparse.SUPPRESS)
    parser.add_argument(
        "-q",
        "--quality",
        default=None,
        help="Render quality preset (defaults to 'low' or the stored value when resuming)",
    )
    parser.add_argument(
        "-n",
        "--num-bodies",
        type=int,
        default=None,
        help="Number of bodies to spawn",
    )
    parser.add_argument(
        "-t",
        "--seconds",
        type=float,
        default=None,
        help="Duration of the simulation in seconds",
    )
    parser.add_argument(
        "-r",
        "--resume",
        type=Path,
        help="Reuse an existing run directory; skips Chrono when physics data already exists",
    )
    parser.add_argument(
        "-ph",
        "--physics-from",
        type=Path,
        help="Reuse physics data from an existing output folder (renders without running Chrono)",
    )
    parser.add_argument(
        "-s",
        "--stitch",
        type=Path,
        help="Only stitch an mp4 from existing rendered frames in the given run directory",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help="Path to JSON config to override defaults for this run",
    )
    parser.add_argument(
        "-b",
        "--blender-scene",
        type=Path,
        help="Path to a .blend file to load before rendering (overrides config)",
    )
    parser.add_argument(
        "-p",
        "--prep-scene",
        action="store_true",
        help="Export Alembic and save an editable .blend, then stop before rendering",
    )
    parser.add_argument(
        "--scene-out",
        type=Path,
        help="Path to save the editable .blend (defaults to run_dir/scene_edit.blend when --prep-scene)",
    )
    parser.add_argument(
        "-f",
        "--first-frame",
        action="store_true",
        help="Render only the first frame",
    )
    return parser.parse_args()


def detect_resume_frame(run_dir: Path, metadata: dict, logger) -> int:
    """Find the next frame index to render based on existing frame_XXXX.png files."""
    frames_dir = run_dir / "rendered_frames"
    highest = -1
    if frames_dir.exists():
        for png in frames_dir.glob("frame_*.png"):
            suffix = png.stem.replace("frame_", "")
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    if highest >= 0:
        next_frame = highest + 1
        last_frame = max(0, metadata.get("frames", 0) - 1)
        if next_frame > last_frame:
            logger.info(
                "All frames already rendered (last frame %d, existing max %d). Nothing to do.",
                last_frame,
                highest,
            )
            sys.exit(0)
        logger.info("Resuming rendering from frame %d (last completed frame %d)", next_frame, highest)
        return next_frame
    return 0


def main():
    args = parse_args()

    if (args.resume and args.stitch) or (args.physics_from and args.stitch):
        print("Choose either --resume/--physics-from or --stitch, not both.", file=sys.stderr)
        sys.exit(1)
    if args.resume and args.physics_from:
        print("Choose either --resume or --physics-from, not both.", file=sys.stderr)
        sys.exit(1)

    # Prefer an explicit config, else fall back to the source run's config when reusing physics,
    # else defaults.
    config_override_path = args.config
    if args.physics_from and config_override_path is None:
        candidate_cfg = Path(args.physics_from).expanduser() / "config_used.json"
        if candidate_cfg.exists():
            config_override_path = candidate_cfg
    config = load_config(config_override_path)
    if args.stitch:
        run_dir = Path(args.stitch).expanduser()
        if not run_dir.exists():
            print(f"Stitch target not found: {run_dir}", file=sys.stderr)
            sys.exit(1)
        if args.config is None:
            saved_cfg = run_dir / "config_used.json"
            if saved_cfg.exists():
                config = load_config(saved_cfg)
        meta_path = run_dir / "run_metadata.json"
        if not meta_path.exists():
            print(f"Stitch requested but missing metadata at {meta_path}", file=sys.stderr)
            sys.exit(1)
        metadata = json.loads(meta_path.read_text())
        presets = config.get("quality_presets", {})
        fallback_quality = metadata.get("quality") or config.get("default_quality", "high")
        preset = presets.get(fallback_quality) or presets.get("high") or {}
        frame_rate = int(metadata.get("frame_rate") or preset.get("fps", 30))
        logger = setup_logger(run_dir)
        logger.info("Stitch-only mode: %s", run_dir)
        logger.info("Using frame rate: %d fps", frame_rate)
        run_ffmpeg(run_dir, frame_rate, logger, config)
        logger.info("Stitch-only mode complete. Outputs in %s", run_dir)
        print("Done.")
        return

    output_root = Path(config["output_root"]).expanduser()
    run_dir = Path(args.resume).expanduser() if args.resume else next_run_dir(output_root)
    if args.resume and not args.config:
        saved_cfg = run_dir / "config_used.json"
        if saved_cfg.exists():
            config = load_config(saved_cfg)
            output_root = Path(config["output_root"]).expanduser()

    quality = args.quality or config.get("default_quality") or DEFAULT_CONFIG.get("default_quality", "low")
    duration_seconds = args.seconds if args.seconds is not None else config.get(
        "duration_seconds", DEFAULT_CONFIG["duration_seconds"]
    )
    num_bodies = args.num_bodies if args.num_bodies is not None else config.get(
        "default_body_count", DEFAULT_CONFIG["default_body_count"]
    )
    (run_dir / "physics").mkdir(parents=True, exist_ok=True)
    (run_dir / "alembic").mkdir(parents=True, exist_ok=True)
    (run_dir / "rendered_frames").mkdir(parents=True, exist_ok=True)
    save_config(config, run_dir / "config_used.json")
    scene_override = Path(args.blender_scene).expanduser() if args.blender_scene else None
    scene_output_path = Path(args.scene_out).expanduser() if args.scene_out else None
    if scene_output_path is None:
        cfg_scene_out = config.get("blender_scene_output")
        if cfg_scene_out:
            scene_output_path = Path(cfg_scene_out).expanduser()
    stop_after_scene = args.prep_scene or bool(config.get("blender_stop_after_scene", False))
    if stop_after_scene and scene_output_path is None:
        scene_output_path = run_dir / "scene_edit.blend"
    preserve_materials = bool(config.get("blender_preserve_materials", False))
    # Tee stdout/stderr to run.log to capture all output.
    log_file = (run_dir / "run.log").open("a", buffering=1)

    class _Tee(io.TextIOBase):
        def __init__(self, stream, logfile):
            self.stream = stream
            self.logfile = logfile

        def write(self, s):
            self.stream.write(s)
            self.logfile.write(s)
            self.stream.flush()
            self.logfile.flush()
            return len(s)

        def flush(self):
            self.stream.flush()
            self.logfile.flush()

    sys.stdout = _Tee(sys.stdout, log_file)
    sys.stderr = _Tee(sys.stderr, log_file)
    # Write a stub metadata early so other tools can inspect last-run intent even if the run fails.
    if not args.resume and not args.physics_from:
        preset = config["quality_presets"].get(quality, config["quality_presets"].get("high", {}))
        frame_rate_stub = preset.get("fps", 30)
        stub_meta = {
            "quality": quality,
            "duration_seconds": duration_seconds,
            "body_count": num_bodies,
            "frame_rate": frame_rate_stub,
            "frames": int(duration_seconds * frame_rate_stub),
        }
        (run_dir / "run_metadata.json").write_text(json.dumps(stub_meta, indent=2))

    logger = setup_logger(run_dir, to_file=False)
    resume_frame = 0

    if args.resume:
        chrono_result = load_existing_run(run_dir, logger)
        quality = args.quality or chrono_result["metadata"].get("quality", quality)
        preset = config["quality_presets"].get(quality, config["quality_presets"]["high"])
        frame_rate = chrono_result["metadata"].get("frame_rate", preset.get("fps", 30))
        chrono_result["metadata"]["quality"] = quality
        (run_dir / "run_metadata.json").write_text(json.dumps(chrono_result["metadata"], indent=2))
        # First-frame mode always (re)renders frame 0 — ignore the resume scan so
        # a lighting test frame can be regenerated even when frames already exist.
        if args.first_frame:
            resume_frame = 0
        else:
            resume_frame = detect_resume_frame(run_dir, chrono_result["metadata"], logger)
        logger.info(
            "Resuming run directory: %s (quality=%s, duration=%.2fs, bodies=%d)",
            run_dir,
            quality,
            chrono_result["metadata"].get("duration_seconds", 0.0),
            chrono_result["metadata"].get("body_count", 0),
        )
    elif args.physics_from:
        src_dir = Path(args.physics_from).expanduser()
        if not src_dir.exists():
            print(f"Physics source not found: {src_dir}", file=sys.stderr)
            sys.exit(1)
        chrono_result = load_existing_run(src_dir, logger)
        preset = config["quality_presets"].get(quality, config["quality_presets"].get("high", {}))
        frame_rate = chrono_result["metadata"].get("frame_rate") or preset.get("fps", 30)
        chrono_result["metadata"]["quality"] = quality
        if chrono_result["metadata"].get("frame_rate") is None:
            chrono_result["metadata"]["frame_rate"] = frame_rate
        if chrono_result["metadata"].get("duration_seconds") is None and chrono_result["metadata"].get("frames"):
            chrono_result["metadata"]["duration_seconds"] = chrono_result["metadata"]["frames"] / frame_rate
        # Copy physics data into this run directory for provenance
        src_npz = chrono_result["npz_path"]
        dest_npz = run_dir / "physics" / "motion_data.npz"
        shutil.copyfile(src_npz, dest_npz)
        chrono_result["npz_path"] = dest_npz
        (run_dir / "run_metadata.json").write_text(json.dumps(chrono_result["metadata"], indent=2))
        resume_frame = 0
        logger.info(
            "Reusing physics from %s (quality=%s, fps=%s, bodies=%d)",
            src_dir,
            quality,
            frame_rate,
            chrono_result["metadata"].get("body_count", 0),
        )
    else:
        preset = config["quality_presets"].get(quality, config["quality_presets"]["high"])
        frame_rate = preset.get("fps", 30)
        logger.info(
            "New run directory: %s (quality=%s, duration=%.2fs, bodies=%d)",
            run_dir,
            quality,
            duration_seconds,
            num_bodies,
        )
        os.environ["BODY_COUNT"] = str(num_bodies)
        physics_hz = preset.get("hz", config.get("dynamics_hz", frame_rate))
        chrono_result = run_chrono_sim(run_dir, logger, duration_seconds, frame_rate, physics_hz, config)
        chrono_result["metadata"]["quality"] = quality
        (run_dir / "run_metadata.json").write_text(json.dumps(chrono_result["metadata"], indent=2))

    logger.info("=== Alembic/Render ===")
    blender_script = write_blender_driver(
        run_dir,
        chrono_result["npz_path"],
        chrono_result["metadata"],
        quality,
        frame_rate,
        args.first_frame,
        resume_frame,
        config,
        scene_path=scene_override,
        scene_output_path=scene_output_path,
        stop_after_scene=stop_after_scene,
        preserve_materials=preserve_materials,
    )
    blender_bin = find_blender(logger, config)
    run_blender(blender_bin, blender_script, run_dir, logger)
    if not args.first_frame and not stop_after_scene:
        run_ffmpeg(run_dir, frame_rate, logger, config)

    logger.info("All stages complete. Outputs in %s", run_dir)
    print("Done.")


if __name__ == "__main__":
    main()
