import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Union


DEFAULT_CONFIG: Dict[str, Any] = {
    "dynamics_hz": 60,
    "duration_seconds": 1.5,
    "output_root": "output",
    "default_quality": "low",
    "quality_presets": {
        "low": {"samples": 8, "res_x": 640, "res_y": 360, "fps": 15, "hz": 15, "denoise": False, "mblur": False},
        "high": {"samples": 128, "res_x": 1920, "res_y": 1080, "fps": 30, "hz": 30, "denoise": False, "mblur": False},
        "final": {"samples": 512, "res_x": 3840, "res_y": 2160, "fps": 60, "hz": 60, "denoise": True, "mblur": False},
    },
    "default_body_count": 1,
    "shape_weights": {"box": 0, "sphere": 1, "cylinder": 0},
    "shape_dim_config": {
        "sphere": {"radius_min": 0.1, "radius_max": 0.6},
        "box": {"min": (0.3, 0.3, 0.3), "max": (0.8, 0.8, 0.8)},
        "cylinder": {"radius_min": 0.3, "radius_max": 0.5, "height_min": 0.2, "height_max": 0.9},
    },
    "gravity_const": 0.0005,
    "body_density": 1000.0,
    "spawn_sphere_radius": 5.0,
    "spawn_lin_vel_range": (-3, 3),
    "spawn_ang_vel_range": (-1.04, 1.04),
    "camera_radius": 40.0,
    "light_configs": [
        {"type": "POINT", "pos": (2, -2, 12), "energy": 200},
    ],
    "obstacle_configs": [],
    "obstacle_color": (0.2, 0.22, 0.26),
    "ground_size": (40.0, 1.0, 40.0),
    "ground_center": (0.0, -0.5, 0.0),
    "blender_candidates": [
        os.getenv("BLENDER_BIN"),
        str(Path(".") / "blender-4.2.0-linux-x64" / "blender"),
        "blender",
    ],
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(base[key], val)
        else:
            base[key] = val
    return base


def load_config(path: Optional[Union[Path, str]] = None) -> Dict[str, Any]:
    """Load config from JSON file and merge onto defaults."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if path:
        cfg_path = Path(path).expanduser()
        if not cfg_path.exists():
            print(f"Config override not found: {cfg_path}", file=sys.stderr)
            sys.exit(1)
        try:
            override = json.loads(cfg_path.read_text())
        except json.JSONDecodeError as exc:
            print(f"Failed to parse config file {cfg_path}: {exc}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(override, dict):
            print(f"Config file must contain a JSON object: {cfg_path}", file=sys.stderr)
            sys.exit(1)
        cfg = _deep_merge(cfg, override)
    return cfg


def save_config(config: Dict[str, Any], dest: Union[Path, str]) -> None:
    """Persist the resolved config for a run."""
    dest_path = Path(dest)
    dest_path.write_text(json.dumps(config, indent=2))
