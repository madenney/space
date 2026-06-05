"""Read-only access to the pipeline's output/ directory.

The filesystem is the source of truth (per project decision): each run lives in
output/outputN/ and we read its metadata, config, frames and video straight off
disk. No database, no cached index.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Reuse the pipeline's own config so OUTPUT_ROOT matches what run.py uses.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import DEFAULT_CONFIG  # noqa: E402

OUTPUT_ROOT = Path(DEFAULT_CONFIG["output_root"]).expanduser()
_RUN_RE = re.compile(r"^output(\d+)$")
_FRAME_RE = re.compile(r"^frame_(\d+)\.png$")


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def run_dir_for(run_id: int) -> Path:
    return OUTPUT_ROOT / f"output{run_id}"


def list_frame_indices(run_dir: Path) -> List[int]:
    frames_dir = run_dir / "rendered_frames"
    if not frames_dir.exists():
        return []
    indices: List[int] = []
    for png in frames_dir.glob("frame_*.png"):
        m = _FRAME_RE.match(png.name)
        if m:
            indices.append(int(m.group(1)))
    indices.sort()
    return indices


def frame_path(run_dir: Path, index: int) -> Optional[Path]:
    candidate = run_dir / "rendered_frames" / f"frame_{index:04d}.png"
    return candidate if candidate.exists() else None


def video_path(run_dir: Path) -> Optional[Path]:
    candidate = run_dir / "rendered_frames.mp4"
    return candidate if candidate.exists() else None


def _summarize(run_dir: Path, run_id: int) -> Dict[str, Any]:
    meta = _read_json(run_dir / "run_metadata.json")
    frames = list_frame_indices(run_dir)
    vid = video_path(run_dir)
    return {
        "id": run_id,
        "name": run_dir.name,
        "quality": meta.get("quality"),
        "body_count": meta.get("body_count"),
        "frames_total": meta.get("frames"),
        "frames_rendered": len(frames),
        "frame_rate": meta.get("frame_rate"),
        "duration_seconds": meta.get("duration_seconds"),
        "seed": meta.get("seed"),
        "has_video": vid is not None,
        "modified_at": run_dir.stat().st_mtime,
    }


def list_runs() -> List[Dict[str, Any]]:
    if not OUTPUT_ROOT.exists():
        return []
    runs: List[Dict[str, Any]] = []
    for child in OUTPUT_ROOT.iterdir():
        if not child.is_dir():
            continue
        m = _RUN_RE.match(child.name)
        if not m:
            continue
        runs.append(_summarize(child, int(m.group(1))))
    runs.sort(key=lambda r: r["id"], reverse=True)
    return runs


def run_detail(run_id: int) -> Optional[Dict[str, Any]]:
    run_dir = run_dir_for(run_id)
    if not run_dir.is_dir():
        return None
    summary = _summarize(run_dir, run_id)
    summary["config_used"] = _read_json(run_dir / "config_used.json")
    summary["metadata"] = _read_json(run_dir / "run_metadata.json")
    summary["frame_indices"] = list_frame_indices(run_dir)
    return summary
