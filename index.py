#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from blender_driver import find_blender, run_blender, write_blender_driver
from config import load_config, save_config
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
    if not meta_path.exists():
        logger.error("Resume requested but missing metadata at %s", meta_path)
        sys.exit(1)
    metadata = json.loads(meta_path.read_text())
    logger.info("Using existing physics data: %s", npz_path)
    return {"npz_path": npz_path, "metadata": metadata}


def parse_args():
    parser = argparse.ArgumentParser(description="Chrono -> Alembic -> Blender render pipeline")
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
        "-c",
        "--config",
        type=Path,
        help="Path to JSON config to override defaults for this run",
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

    config = load_config(args.config)
    output_root = Path(config["output_root"]).expanduser()
    run_dir = Path(args.resume).expanduser() if args.resume else next_run_dir(output_root)
    if args.resume and not args.config:
        saved_cfg = run_dir / "config_used.json"
        if saved_cfg.exists():
            config = load_config(saved_cfg)
            output_root = Path(config["output_root"]).expanduser()

    quality = args.quality or config.get("default_quality", "low")
    duration_seconds = args.seconds if args.seconds is not None else config["duration_seconds"]
    num_bodies = args.num_bodies if args.num_bodies is not None else config["default_body_count"]
    (run_dir / "physics").mkdir(parents=True, exist_ok=True)
    (run_dir / "alembic").mkdir(parents=True, exist_ok=True)
    (run_dir / "rendered_frames").mkdir(parents=True, exist_ok=True)
    save_config(config, run_dir / "config_used.json")

    logger = setup_logger(run_dir)
    resume_frame = 0

    if args.resume:
        chrono_result = load_existing_run(run_dir, logger)
        quality = args.quality or chrono_result["metadata"].get("quality", quality)
        preset = config["quality_presets"].get(quality, config["quality_presets"]["high"])
        frame_rate = chrono_result["metadata"].get("frame_rate", preset.get("fps", 30))
        chrono_result["metadata"]["quality"] = quality
        (run_dir / "run_metadata.json").write_text(json.dumps(chrono_result["metadata"], indent=2))
        resume_frame = detect_resume_frame(run_dir, chrono_result["metadata"], logger)
        logger.info(
            "Resuming run directory: %s (quality=%s, duration=%.2fs, bodies=%d)",
            run_dir,
            quality,
            chrono_result["metadata"].get("duration_seconds", 0.0),
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
    )
    blender_bin = find_blender(logger, config)
    run_blender(blender_bin, blender_script, run_dir, logger)
    if not args.first_frame:
        run_ffmpeg(run_dir, frame_rate, logger, config)

    logger.info("All stages complete. Outputs in %s", run_dir)
    print("Done.")


if __name__ == "__main__":
    main()
