import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Union


PROJECT_ROOT = Path(__file__).resolve().parent


DEFAULT_CONFIG: Dict[str, Any] = {
    # Output / render presets
    "output_root": str(PROJECT_ROOT / "output"),
    "default_quality": "low",
    "quality_presets": {
        "low": {"samples": 8, "res_x": 640, "res_y": 360, "fps": 15, "hz": 15, "denoise": False, "mblur": False},
        "high": {"samples": 128, "res_x": 1920, "res_y": 1080, "fps": 30, "hz": 30, "denoise": False, "mblur": False},
        "final": {"samples": 128, "res_x": 3840, "res_y": 2160, "fps": 60, "hz": 60, "denoise": True, "mblur": False},
    },

    # Simulation / physics
    "dynamics_hz": 60,
    "duration_seconds": 1,
    # Gravity is normalized by total mass (see physics.py), so this is a
    # body-count-INDEPENDENT strength: ~500 + spawn speed ~[2,5] gives a bound,
    # lively swarm at any N. ≳2000 collapses hard enough to destabilize the sim.
    "gravity_const": 500.0,
    # Softening length for the N-body attraction: forces are capped as bodies get
    # within this distance, preventing close-encounter blow-ups. Larger = gentler.
    "gravity_softening": 1.0,
    "body_density": 1000.0,
    "spawn_sphere_radius": 20.0,
    # Speed MAGNITUDE band (direction randomized). [2,5] balances against
    # gravity ~500 for a bound, churning swarm rather than a fly-apart cloud.
    "spawn_lin_vel_range": (2, 5),
    "spawn_ang_vel_range": (-5, 5),

    # Bodies / spawning
    "default_body_count": 1,
    "shape_weights": {"box": 0.1, "sphere": 0.9, "cylinder": 0},
    "shape_dim_config": {
        "sphere": {"radius_min": 0.1, "radius_max": 1},
        "box": {"min": (0.5, 0.5, 0.5), "max": (1, 1, 1)},
        "cylinder": {"radius_min": 0.3, "radius_max": 0.5, "height_min": 0.2, "height_max": 0.9},
    },

    # Camera / ground / obstacles
    "camera_radius": 40.0,      # distance from the scene center
    "camera_azimuth": 0.0,      # horizontal angle around the scene (deg); 0 = front
    "camera_elevation": 12.0,   # vertical angle above the horizon (deg)
    # Keep the swarm centered: each frame the camera locks onto the densest clump
    # (robust to escaping bodies, unlike the mass-mean) and translates with it at
    # a fixed distance/angle — no zoom. False = static camera at origin.
    "camera_track_cog": False,
    # Smoothing window (seconds) for the tracked target, to de-shake the camera.
    # Centered/lag-free. 0 = raw (shaky); larger = glassier but less responsive.
    "camera_smooth_seconds": 0.5,
    "camera_lens_mm": 35,
    "camera_fstop": 4.0,
    # First-class camera-move spec. None => derive from the flat keys above
    # (camera_track_cog -> "track", else "static"), preserving old behavior.
    # Set a dict to author a move; interpreted in blender_stage.py:
    #   {"mode": "static"}                              fixed placement
    #   {"mode": "track"}                               fixed offset, follow look_at
    #   {"mode": "orbit", "orbit_degrees": 360,
    #    "radius_to": 25, "elevation_to": 40}           turntable (+ optional drift)
    #   {"mode": "keyframes", "keyframes": [            authored path (spherical)
    #      {"t": 0, "radius": 70, "azimuth": 0,  "elevation": 8},
    #      {"t": 6, "radius": 30, "azimuth": 120,"elevation": 30, "ease": "inout"}]}
    # All modes share the base placement (camera_radius/azimuth/elevation) as the
    # starting point; orbit/keyframes are positioned RELATIVE to look_at.
    "camera_move": None,
    # Where the camera points: "origin" | "clump" (densest swarm) | [x, y, z].
    # None => "clump" when camera_track_cog else "origin".
    "camera_look_at": None,
    # Drop a small static red cube at world origin (0,0,0) as a visual marker.
    # Render-only; never in the sim, so it doesn't move or collide.
    "show_origin_marker": False,
    "ground_size": (40.0, 1.0, 40.0),
    "ground_center": (0.0, -0.5, 0.0),
    "obstacle_configs": [],
    "obstacle_color": (0.2, 0.22, 0.26),

    # Lighting / environment
    # Soft studio setup for orbs in gray space: big AREA lights give gentle,
    # wraparound light with soft shadows. `size` is the softness dial (bigger =
    # softer edges/shadows). Lights auto-aim at the origin in blender_driver.
    "light_configs": [
        {"type": "AREA", "pos": (14, -14, 16), "energy": 3000, "size": 18},  # key (upper front)
        {"type": "AREA", "pos": (-16, -10, 4), "energy": 1500, "size": 25},  # fill (opposite, softer)
        {"type": "AREA", "pos": (6, 16, 10),  "energy": 2200, "size": 12},   # rim/back (separation)
    ],
    # Gray "void" backdrop + ambient fill so shadows don't crush to black.
    "world_color": (0.05, 0.05, 0.055, 1.0),
    "hdri_path": None,
    "hdri_strength": 1.0,
    "blender_environment": None,
    # Optional .blend scene override for the render stage
    "blender_scene": None,
    "blender_scene_use_camera": True,
    "blender_scene_use_lights": True,
    "blender_scene_use_world": True,
    "blender_scene_output": None,
    "blender_stop_after_scene": False,
    "blender_preserve_materials": False,

    # Blender binary candidates
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
