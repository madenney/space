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
        # Ultra-cheap "rough physics" preview: tiny res, almost no samples, low fps.
        # Image looks rough, but it's fast — use it to judge motion/timing only.
        "draft": {"samples": 3, "res_x": 384, "res_y": 216, "fps": 8, "hz": 15, "denoise": False, "mblur": False},
        "low": {"samples": 8, "res_x": 640, "res_y": 360, "fps": 15, "hz": 15, "denoise": False, "mblur": False},
        "high": {"samples": 128, "res_x": 1920, "res_y": 1080, "fps": 30, "hz": 30, "denoise": False, "mblur": False},
        "final": {"samples": 128, "res_x": 3840, "res_y": 2160, "fps": 60, "hz": 60, "denoise": True, "mblur": False},
    },

    # Simulation / physics
    # Which scenario produces the motion (see scenarios.py):
    #   "rigid"   - Chrono rigid bodies + contact + N-body gravity (legacy cache)
    #   "gravity" - vectorized NumPy point-particle N-body, scales to thousands
    "scenario": "rigid",
    # Render rigid bodies via Geometry-Nodes instancing (real shapes + rotation +
    # size, O(buckets) objects) instead of N real objects + per-frame fcurves +
    # Alembic. Required above ~1k bodies, where the real-object path OOMs. The
    # real-object path stays the default so the editable-.blend/Alembic workflow
    # is unaffected for small scenes.
    "render_instanced": False,
    # Render radius range for "gravity"/"collide" point particles.
    "particle_radius_range": (0.2, 0.6),
    # Soft-sphere collision (the "collide" scenario): stiffer spring = less overlap
    # but needs the fine internal timestep below; more damping = less bouncy.
    "collision_stiffness": 20000.0,
    "collision_damping": 30.0,
    "collision_physics_hz": 240,
    "dynamics_hz": 60,
    "duration_seconds": 1,
    # Gravity is normalized by total mass (see physics.py), so this is a
    # body-count-INDEPENDENT strength: ~500 + spawn speed ~[2,5] gives a bound,
    # lively swarm at any N. ≳2000 collapses hard enough to destabilize the sim.
    "gravity_const": 500.0,
    # Softening length for the N-body attraction: forces are capped as bodies get
    # within this distance, preventing close-encounter blow-ups. Larger = gentler.
    "gravity_softening": 1.0,
    # Gravity solver: "auto" (tree once N>=2500), "exact" (O(N^2), bit-faithful),
    # or "tree" (Barnes-Hut O(N log N), ~0.5% force error, scales to many thousands).
    # bh_theta is the tree opening angle: smaller = more accurate + slower (0.5 std).
    "gravity_solver": "auto",
    "bh_theta": 0.5,
    # Hard cap on per-body speed (sim units/sec); 0 = off. A safety net against a
    # degenerate high-speed contact slingshotting a body to escape speed and then
    # blowing the whole sim to NaN through the all-pairs gravity sum.
    "max_body_speed": 0.0,
    # Static checkered enclosure (camera-ray only) to make camera motion legible.
    # Push-in: floor the framing radius at this fraction of its peak so the camera
    # stops diving once the clump condenses (otherwise it burrows into the core).
    "camera_pushin_radius_floor": 0.3,
    # Camera framing ignores the farthest (1 - this) fraction of bodies as outliers
    # (escapees/slingshots), so a few far-flung bodies don't blow the shot wide.
    "camera_outlier_keep": 0.95,
    "reference_env": False,
    "reference_env_size": 500.0,    # half-extent of the box (must contain the action + camera)
    "reference_env_checks": 24.0,   # checker squares per box edge
    "body_density": 1000.0,
    "spawn_sphere_radius": 20.0,
    # Lumpy spawn: scatter bodies around this many cluster centers instead of
    # filling the sphere evenly (1 = uniform). Velocities stay radial, so it still
    # explodes -- but as distinct lumps that collide off-center on the way back in.
    # Swirl: inject a coherent tangential launch (axis x r̂) so the cloud starts
    # with net angular momentum and spins up as it collapses. TWO ways to set it:
    #   swirl_speed (preferred): an absolute tangential speed ADDED on top of the
    #     full outward kick. Explosion and spin are independent -> balls fly away
    #     AND rotate. Compare to spawn_lin_vel_range: equal = 45deg launch.
    #   spin_fraction (legacy): BLENDS tangential into the radial direction, so it
    #     steals from the outward kick (high spin cancels the explosion). 0..1.
    # Set swirl_speed for new work; spin_fraction is kept for old configs. spin_axis
    # is the rotation axis (sim Y-up by default). swirl_speed > 0 wins if both set.
    "swirl_speed": 0.0,
    "spin_fraction": 0.0,
    "spin_axis": (0.0, 1.0, 0.0),
    "spawn_clusters": 1,
    "spawn_cluster_spread": 0.6,        # how far cluster centers spread (frac of spawn radius)
    "spawn_cluster_radius_frac": 0.22,  # size of each lump (frac of spawn radius)
    # Optional dominant central body: one heavy, large, stationary seed spawned at
    # the origin with zero velocity — a proto-"sun" for the exploding cloud to fall
    # back onto / swing around. Being the heaviest, it sinks to the centre of the
    # potential well. mass is in the same unit as a cloud body (each = 1). 0 = off.
    "central_mass": 0.0,
    "central_radius": 5.0,
    "central_color": (1.0, 0.85, 0.3),
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
    # Auto-framing ("fit" camera move): distance = fit_scale × the radius enclosing
    # fit_percentile% of bodies, so the swarm stays a consistent size as it expands
    # /collapses. Lower fit_scale = tighter framing; min_distance is the zoom-in floor.
    "camera_fit_scale": 3.5,
    "camera_fit_percentile": 90,
    "camera_fit_min_distance": 8.0,
    # "pushin" camera: wide (fit_scale) until peak expansion, then ease the framing
    # down to close_scale so the camera dives in on the collapsing clump.
    "camera_pushin_close_scale": 1.5,
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
    "origin_marker_size": 1.0,  # cube edge length; size up for large-scale scenes
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


# Single source of truth for the tunables the studio builder exposes. The web UI
# renders its form from this (served at /api/config/fields) instead of hardcoding
# the same keys/labels — so adding a knob is ONE entry here next to its default.
#   type: "number" -> scalar; "range" -> a [min,max] magnitude band; "bool"
#   group: which builder section it appears under
FIELD_SCHEMA = [
    # Physics
    {"key": "gravity_const", "label": "gravity", "group": "physics", "type": "number",
     "step": 0.0001, "min": 0,
     "hint": "attraction strength, independent of body count (~500 lively; ≳2000 unstable)"},
    {"key": "gravity_softening", "label": "gravity softening", "group": "physics", "type": "number",
     "step": 0.1, "min": 0,
     "hint": "cushions close encounters so the sim can't blow up (bigger = gentler)"},
    {"key": "spawn_lin_vel_range", "label": "move speed", "group": "physics", "type": "range",
     "step": 0.5, "min": 0, "hint": "initial linear speed range (min → max)"},
    {"key": "spawn_ang_vel_range", "label": "spin speed", "group": "physics", "type": "range",
     "step": 0.5, "min": 0, "hint": "initial angular speed range (min → max)"},
    # Camera
    {"key": "camera_radius", "label": "distance", "group": "camera", "type": "number",
     "step": 1, "min": 1, "hint": "how far back the camera sits"},
    {"key": "camera_azimuth", "label": "azimuth°", "group": "camera", "type": "number",
     "step": 5, "hint": "spin around the scene (0 = front)"},
    {"key": "camera_elevation", "label": "elevation°", "group": "camera", "type": "number",
     "step": 5, "hint": "height angle (− below, + above)"},
    {"key": "camera_smooth_seconds", "label": "cam smoothing (s)", "group": "camera", "type": "number",
     "step": 0.1, "min": 0, "hint": "de-shakes clump tracking; 0 = raw, higher = smoother"},
    {"key": "camera_lens_mm", "label": "lens (mm)", "group": "camera", "type": "number",
     "step": 1, "min": 1, "hint": "focal length; lower = wider"},
    {"key": "camera_fstop", "label": "f-stop", "group": "camera", "type": "number",
     "step": 0.1, "min": 0.1, "hint": "depth of field; lower = blurrier background"},
    {"key": "camera_track_cog", "label": "keep swarm centered", "group": "camera", "type": "bool",
     "hint": "locks onto the densest clump at a fixed distance"},
    {"key": "show_origin_marker", "label": "origin marker", "group": "camera", "type": "bool",
     "hint": "static red cube at 0,0,0"},
]

# Scenarios the studio offers (value must match a key in scenarios.SCENARIOS).
# Kept here (not imported from scenarios.py) so the web layer can serve it without
# pulling in numpy/pychrono.
SCENARIO_CHOICES = [
    {"value": "rigid", "label": "rigid bodies — Chrono, true hard collisions (zero overlap)"},
    {"value": "gravity", "label": "gravity particles — fast, no collisions, scales to thousands"},
    {"value": "collide", "label": "gravity + collisions — soft-sphere, fast, scales to thousands"},
]

# The camera move is a nested spec (camera_move / camera_look_at), so it gets a
# dedicated widget rather than a flat field. This describes the modes and their
# parameters so the UI can render them from data too.
CAMERA_MOVE_SCHEMA = {
    "look_at": {"label": "look at", "options": ["clump", "origin"]},
    "modes": [
        {"mode": "static", "label": "static (fixed)", "params": []},
        {"mode": "track", "label": "track (follow look-at)", "params": []},
        {"mode": "fit", "label": "auto-fit (frame the whole cloud)", "params": [
            {"key": "fit_scale", "label": "fit zoom", "step": 0.5, "default": 3.5},
        ]},
        {"mode": "pushin", "label": "push-in (wide, then zoom to the clump at peak)", "params": [
            {"key": "fit_scale", "label": "wide zoom", "step": 0.5, "default": 3.5},
            {"key": "close_scale", "label": "close zoom", "step": 0.5, "default": 1.5},
        ]},
        {"mode": "orbit", "label": "orbit (turntable)", "params": [
            {"key": "orbit_degrees", "label": "sweep°", "step": 15, "default": 360},
            {"key": "radius_to", "label": "end distance", "step": 1, "optional": True},
            {"key": "elevation_to", "label": "end height°", "step": 5, "optional": True},
        ]},
        {"mode": "keyframes", "label": "keyframes (authored)", "params": [],
         "note": "author waypoints in config_override for now"},
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
