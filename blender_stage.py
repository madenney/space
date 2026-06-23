#!/usr/bin/env python
"""Blender headless render stage for one simulation run.

Static, standalone Python — NOT generated. Reads everything it needs for a run
from that run's own artifacts (``config_used.json``, ``run_metadata.json``, the
physics NPZ) plus a few runtime flags. Invoke through Blender:

    blender -b --python-exit-code 1 -P blender_stage.py -- \
        --run-dir output/output28 --quality final [--resume-frame 1423] \
        [--first-frame] [--stop-after-scene] [--preserve-materials] \
        [--scene-path scene.blend] [--scene-output out.blend]

Because it's an ordinary file (not an f-string template) you can lint it, set
breakpoints, and run it by hand against any existing run directory for debugging.

Pipeline: load NPZ -> build animated meshes -> export Alembic -> reimport ->
rebuild scene (materials/obstacles/world/camera/lights) -> render frames.
"""
import argparse
import math
import os
import sys
import shutil
from pathlib import Path

import bpy
import mathutils
import numpy as np
import json

# Blender forwards everything after a literal `--` to the script's argv.
_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
_p = argparse.ArgumentParser(description="Blender render stage for a sim run")
_p.add_argument("--run-dir", required=True, help="output/outputN directory")
_p.add_argument("--quality", default="high", help="quality preset key")
_p.add_argument("--resume-frame", type=int, default=0, help="first frame to render")
_p.add_argument("--first-frame", action="store_true", help="render only one frame")
_p.add_argument("--stop-after-scene", action="store_true", help="export .blend, skip render")
_p.add_argument("--preserve-materials", action="store_true", help="keep scene materials")
_p.add_argument("--scene-path", default="", help="optional .blend to render from")
_p.add_argument("--scene-output", default="", help="optional .blend to save the prepared scene to")
_args = _p.parse_args(_argv)

run_dir = Path(_args.run_dir).expanduser().resolve()
# The motion contract module is copied next to this script in the run dir.
sys.path.insert(0, str(run_dir))
import motion  # noqa: E402

config = json.loads((run_dir / "config_used.json").read_text())
metadata = json.loads((run_dir / "run_metadata.json").read_text())

physics_dir = run_dir / "physics"
alembic_dir = run_dir / "alembic"
render_dir = run_dir / "rendered_frames"
abc_path = alembic_dir / "motion_data.abc"
npy_path = physics_dir / "motion_data.npz"
base_output = run_dir.parent

# Render preset for this quality (samples / resolution / fps / denoise).
_presets = config.get("quality_presets", {})
preset = _presets.get(_args.quality) or _presets.get("high") or {}
if not preset:
    print(f"Quality preset '{_args.quality}' not found in config", flush=True)
    sys.exit(1)

# fps comes from the run's metadata (what physics actually sampled at); fall back
# to the preset only if an older run lacks it.
frame_rate = int(metadata.get("frame_rate") or preset.get("fps", 30))

# Look / scene knobs: a CLI flag wins, else the run's saved config.
hdri_path_cfg = config.get("hdri_path") or ""
use_hdr = os.getenv("HDRI_PATH") or (hdri_path_cfg if hdri_path_cfg else None)
hdri_strength = config.get("hdri_strength", 1.0)
env_path_str = config.get("blender_environment") or ""
scene_path_str = _args.scene_path or config.get("blender_scene") or ""
scene_use_camera = bool(config.get("blender_scene_use_camera", True))
scene_use_lights = bool(config.get("blender_scene_use_lights", True))
scene_use_world = bool(config.get("blender_scene_use_world", True))
scene_output_path_str = _args.scene_output or config.get("blender_scene_output") or ""
STOP_AFTER_SCENE = _args.stop_after_scene or bool(config.get("blender_stop_after_scene", False))
PRESERVE_MATERIALS = _args.preserve_materials or bool(config.get("blender_preserve_materials", False))
FIRST_FRAME_ONLY = _args.first_frame
frame_start = _args.resume_frame

physics_dir.mkdir(parents=True, exist_ok=True)
alembic_dir.mkdir(parents=True, exist_ok=True)
render_dir.mkdir(parents=True, exist_ok=True)


def resolve_scene_output_path(path_str):
    if not path_str:
        return None
    out_path = Path(path_str)
    if not out_path.is_absolute():
        out_path = run_dir / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return out_path


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def open_scene_file(path_str):
    if not path_str:
        return False
    scene_path = Path(path_str)
    if not scene_path.exists():
        print(f"Scene path not found: {scene_path}")
        return False
    try:
        bpy.ops.wm.open_mainfile(filepath=str(scene_path), load_ui=False)
        print(f"Loaded scene from {scene_path}", flush=True)
        return True
    except Exception as exc:
        print(f"Failed to load scene {scene_path}: {exc}")
        return False


def find_scene_camera(scene):
    if scene.camera:
        return scene.camera
    for obj in scene.objects:
        if obj.type == 'CAMERA':
            return obj
    return None


def scene_has_lights(scene):
    return any(obj.type == 'LIGHT' for obj in scene.objects)


def enable_devices():
    prefs = bpy.context.preferences.addons['cycles'].preferences
    prefs.get_devices()
    # Only attempt backends this Blender build actually supports. Assigning an
    # unsupported value (e.g. METAL on Linux) raises, so filter by the enum and
    # guard each assignment, then fall back to CPU if nothing usable is found.
    try:
        valid_types = {item.identifier for item in prefs.bl_rna.properties['compute_device_type'].enum_items}
    except Exception:
        valid_types = set()
    preferred = ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL")
    chosen = None
    for dev_type in preferred:
        if valid_types and dev_type not in valid_types:
            continue
        try:
            prefs.compute_device_type = dev_type
        except Exception:
            continue
        prefs.get_devices()
        if any(d.type == dev_type for d in prefs.devices):
            chosen = dev_type
            break
    if chosen:
        for device in prefs.devices:
            device.use = device.type == chosen
        bpy.context.scene.cycles.device = 'GPU'
        print(f"Using GPU backend: {chosen}", flush=True)
    else:
        bpy.context.scene.cycles.device = 'CPU'
        print("Falling back to CPU rendering.", flush=True)


def map_pos(vec):
    rot = mathutils.Quaternion((1.0, 0.0, 0.0), math.pi / 2.0)
    return rot @ vec


def map_quat(quat):
    rot = mathutils.Quaternion((1.0, 0.0, 0.0), math.pi / 2.0)
    return rot @ quat @ rot.conjugated()


# ---- Camera spec (first-class) --------------------------------------------
# A camera move is data, not baked logic. resolve_camera() normalizes either the
# new `camera`/`camera_move` config or the legacy flat keys into one spec; the
# build section below interprets it into per-frame keyframes.

def _spherical(radius, azimuth_deg, elevation_deg):
    """Position on a sphere in Chrono space (Y up): azimuth sweeps the horizontal
    plane, elevation lifts above it."""
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    x = radius * math.cos(el) * math.cos(az)
    z = radius * math.cos(el) * math.sin(az)
    y = radius * math.sin(el)
    return mathutils.Vector((x, y, z))


def _lerp(a, b, t):
    return a + (b - a) * t


def _ease(t, kind):
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    if kind == "in":
        return t * t
    if kind == "out":
        return 1.0 - (1.0 - t) * (1.0 - t)
    if kind in ("inout", "in_out", "smooth"):
        return t * t * (3.0 - 2.0 * t)
    return t  # linear


def _interp_keyframes(kfs, t, base_radius, base_az, base_el):
    """Interpolate (radius, azimuth, elevation) at time t seconds across spherical
    waypoints [{t, radius, azimuth, elevation, ease}], clamped at the ends."""
    if not kfs:
        return base_radius, base_az, base_el
    pts = sorted(kfs, key=lambda k: k.get("t", 0.0))

    def field(k, name, default):
        v = k.get(name)
        return float(v) if v is not None else default

    if t <= pts[0].get("t", 0.0):
        k = pts[0]
        return field(k, "radius", base_radius), field(k, "azimuth", base_az), field(k, "elevation", base_el)
    if t >= pts[-1].get("t", 0.0):
        k = pts[-1]
        return field(k, "radius", base_radius), field(k, "azimuth", base_az), field(k, "elevation", base_el)
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        ta, tb = a.get("t", 0.0), b.get("t", 0.0)
        if ta <= t <= tb:
            span = (tb - ta) or 1.0
            frac = _ease((t - ta) / span, b.get("ease", "linear"))
            return (
                _lerp(field(a, "radius", base_radius), field(b, "radius", base_radius), frac),
                _lerp(field(a, "azimuth", base_az), field(b, "azimuth", base_az), frac),
                _lerp(field(a, "elevation", base_el), field(b, "elevation", base_el), frac),
            )
    k = pts[-1]
    return field(k, "radius", base_radius), field(k, "azimuth", base_az), field(k, "elevation", base_el)


def compute_clump_positions(positions, frame_rate, smooth_seconds):
    """Per-frame densest-clump center (median refined to the inner half, then a
    centered temporal smooth). Robust to escaping bodies, unlike the mass-mean.
    positions: (F, N, 3) in sim coordinates."""
    F, B, _ = positions.shape
    out = np.zeros((F, 3))
    keep = max(5, B // 2)
    for f in range(F):
        P = positions[f]
        m = np.median(P, axis=0)
        dm = np.linalg.norm(P - m, axis=1)
        inner = P[np.argsort(dm)[:keep]]      # closest half to the median
        out[f] = inner.mean(axis=0)
    rad = int(round(smooth_seconds * frame_rate / 2.0))
    if rad >= 1 and F > 2:
        cs = np.cumsum(np.vstack([np.zeros((1, 3)), out]), axis=0)
        sm = np.empty_like(out)
        for f in range(F):
            a = max(0, f - rad); b = min(F, f + rad + 1)
            sm[f] = (cs[b] - cs[a]) / (b - a)
        out = sm
    return out


def resolve_camera(config):
    """Normalize camera config into one spec. Precedence: explicit `camera` block
    > `camera_move`/`camera_look_at` > legacy flat keys > defaults."""
    cam = dict(config.get("camera") or {})
    legacy_track = bool(config.get("camera_track_cog", False))

    look_at = cam.get("look_at", config.get("camera_look_at"))
    if look_at is None:
        look_at = "clump" if legacy_track else "origin"

    move = dict(cam.get("move") or config.get("camera_move") or {})
    move.setdefault("radius", config.get("camera_radius", 40.0))
    move.setdefault("azimuth", config.get("camera_azimuth", 0.0))
    move.setdefault("elevation", config.get("camera_elevation", 12.0))
    mode = move.get("mode")
    if mode is None or mode == "auto":
        mode = "track" if legacy_track else "static"
    move["mode"] = mode

    return {
        "look_at": look_at,
        "smooth_seconds": cam.get("smooth_seconds", config.get("camera_smooth_seconds", 0.5)),
        "lens_mm": cam.get("lens_mm", config.get("camera_lens_mm", 35)),
        "fstop": cam.get("fstop", config.get("camera_fstop", 4.0)),
        "move": move,
    }


# Stage 1: load NPZ, build animation, export Alembic (animated bodies only)
print("=== Alembic export ===", flush=True)
clear_scene()
print("Loading physics NPZ...", flush=True)
bodies = metadata.get("bodies") or []

# Load into a unified structure-of-arrays for BOTH the new contract (motion.py)
# and the legacy per-body layout, so the rest of stage 1 is format-agnostic and
# can keyframe in bulk (foreach_set) instead of a per-frame Python loop.
_mo = motion.read_motion(npy_path)
if _mo is not None:
    all_positions = _mo["positions"]                # (F, N, 3) sim coords
    orient = _mo["orientations"]                     # (F, N, 4) wxyz, or None
    frame_numbers = _mo["frame_index"].astype(int)   # (F,)
elif bodies:
    _data = np.load(str(npy_path))
    _keys = [b["name"] for b in bodies]
    all_positions = np.stack([_data[k][:, 2:5] for k in _keys], axis=1)  # (F, N, 3)
    orient = np.stack([_data[k][:, 5:9] for k in _keys], axis=1)         # (F, N, 4)
    frame_numbers = _data[_keys[0]][:, 0].astype(int)                    # (F,)
else:
    all_positions = orient = frame_numbers = None

if all_positions is not None:
    frames_total = int(all_positions.shape[0])
    total_bodies = int(all_positions.shape[1])
else:
    frames_total = int(metadata.get("frames", 0) or 0)
    total_bodies = 0
# Particle scenarios (gravity/collide) render as instanced point clouds instead
# of N separate objects + Alembic — one mesh instanced over the positions, so
# thousands of particles stay cheap. Rigid/legacy keep the real-object path.
PARTICLE_MODE = (metadata.get("scenario") in ("gravity", "collide")) and (all_positions is not None)
# `frames` is a count; last keyed frame index is count-1.
frame_end = max(frame_start, frames_total - 1)
if frame_start > frame_end:
    print(f"Nothing to render: start frame {frame_start} exceeds last frame {frame_end}")
    sys.exit(0)
print(f"Loaded physics data for {total_bodies} bodies, {frames_total} frames", flush=True)

# Resolve the camera spec and, when it tracks/focuses the swarm, precompute the
# smoothed per-frame clump center (densest clump, robust to escaping bodies).
CAM = resolve_camera(config)
clump_positions = None  # (frames, 3) smoothed clump center in sim coords
if CAM["look_at"] == "clump" and all_positions is not None:
    clump_positions = compute_clump_positions(all_positions, frame_rate, CAM["smooth_seconds"])


def create_anim_mesh(body_def):

    shape = body_def["shape"]
    dims = body_def["dims"]

    if shape == "box":
        sx = dims["sx"]
        sy = dims["sy"]
        sz = dims["sz"]
        bpy.ops.mesh.primitive_cube_add(size=1.0, align='WORLD')
        obj = bpy.context.active_object
        obj.scale = (1.0, 1.0, 1.0)
        obj.dimensions = (sx, sy, sz)

    elif shape == "sphere":
        r = dims["radius"]
        # Blender sphere radius = actual radius, so this is a direct match.
        bpy.ops.mesh.primitive_uv_sphere_add(radius=r)
        obj = bpy.context.active_object

    elif shape == "cylinder":
        r = dims["radius"]
        h = dims["height"]
        # Blender cylinder: radius=r, depth=h → matches Chrono's radius/height.
        bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=h)
        obj = bpy.context.active_object

    else:
        raise ValueError(f"Unknown shape type: {shape}")

    obj.name = body_def["name"]
    obj.rotation_mode = 'QUATERNION'
    return obj


# Vectorized sim(Y-up) -> Blender(Z-up) transforms, so keyframes can be set in
# bulk via foreach_set instead of a per-frame Python loop (the scaling bottleneck).
_ROT = np.array([math.cos(math.pi / 4), math.sin(math.pi / 4), 0.0, 0.0])  # 90° about X, wxyz
_ROT_CONJ = _ROT * np.array([1.0, -1.0, -1.0, -1.0])


def _qmul(a, b):
    aw, ax, ay, az = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    bw, bx, by, bz = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    return np.stack([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ], axis=-1)


def _map_pos_arr(p):   # (F,3): (x,y,z) -> (x,-z,y)
    return np.stack([p[:, 0], -p[:, 2], p[:, 1]], axis=-1)


def _map_quat_arr(q):  # (F,4) wxyz
    rot = np.broadcast_to(_ROT, q.shape)
    rotc = np.broadcast_to(_ROT_CONJ, q.shape)
    return _qmul(_qmul(rot, q), rotc)


def _set_fcurves(action, data_path, values):
    """Bulk-write one keyframe per frame for each component of `values` (F, k)."""
    fn = frame_numbers.astype(np.float64)
    nf = values.shape[0]
    for axis in range(values.shape[1]):
        fc = action.fcurves.new(data_path=data_path, index=axis)
        kps = fc.keyframe_points
        kps.add(nf)
        co = np.empty(2 * nf, dtype=np.float64)
        co[0::2] = fn
        co[1::2] = values[:, axis]
        kps.foreach_set("co", co)
        kps.update()


# --- Particle instancing -----------------------------------------------------
# Render N particles as vertex-instanced spheres, bucketed by color and size
# band, so thousands stay cheap (dozens of objects, not N). Positions are driven
# per-frame by a handler reading all_positions in bulk (foreach_set).
_PARTICLE_BUCKETS = []  # (parent_mesh, indices) animated each frame


def _low_sphere_mesh(radius):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, segments=12, ring_count=8)
    o = bpy.context.active_object
    me = bpy.data.meshes.new_from_object(o)
    bpy.data.objects.remove(o, do_unlink=True)
    return me


def _particle_frame_handler(scene, depsgraph=None):
    f0 = int(frame_numbers[0]) if frame_numbers is not None else 0
    row = int(scene.frame_current) - f0
    row = 0 if row < 0 else (frames_total - 1 if row >= frames_total else row)
    snap = all_positions[row]
    for pm, idx in _PARTICLE_BUCKETS:
        pm.vertices.foreach_set("co", _map_pos_arr(snap[idx].astype(np.float64)).reshape(-1))
        pm.update()


def build_particle_instances(scene):
    radii = np.array([b["dims"].get("radius", 0.3) for b in bodies], dtype=np.float64)
    colors = [tuple(b.get("color", (0.8, 0.8, 0.8))) for b in bodies]
    by_color = {}
    for i in range(len(bodies)):
        by_color.setdefault(colors[i], []).append(i)
    bands = 4
    rmin, rmax = float(radii.min()), float(radii.max())
    bw = (rmax - rmin) / bands + 1e-9
    snap0 = all_positions[max(0, min(frame_start, frames_total - 1))]
    nobj = 0
    for col, idxs in by_color.items():
        idxs = np.array(idxs)
        mat = bpy.data.materials.new("ParticleMat")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (col[0], col[1], col[2], 1.0)
            bsdf.inputs["Roughness"].default_value = 0.45
        for band in range(bands):
            lo = rmin + band * bw
            last = band == bands - 1
            in_band = (radii[idxs] >= lo) & (radii[idxs] <= lo + bw + 1e-9) if last else \
                      (radii[idxs] >= lo) & (radii[idxs] < lo + bw)
            sel = idxs[in_band]
            if len(sel) == 0:
                continue
            sph = bpy.data.objects.new("particle_sphere", _low_sphere_mesh(float(radii[sel].mean())))
            sph.data.materials.append(mat)
            scene.collection.objects.link(sph)
            pm = bpy.data.meshes.new("particle_points")
            pm.vertices.add(len(sel))
            pm.vertices.foreach_set("co", _map_pos_arr(snap0[sel].astype(np.float64)).reshape(-1))
            pm.update()
            parent = bpy.data.objects.new("particle_instancer", pm)
            scene.collection.objects.link(parent)
            parent.instance_type = 'VERTS'
            parent.show_instancer_for_render = False
            sph.parent = parent
            sph.location = (0, 0, 0)
            _PARTICLE_BUCKETS.append((pm, sel))
            nobj += 2
    bpy.app.handlers.frame_change_pre.append(_particle_frame_handler)
    print(f"Built {nobj} instancer objects ({len(by_color)} colors x {bands} bands) "
          f"for {len(bodies)} particles", flush=True)


anim_objects = []
if PARTICLE_MODE:
    print("Particle mode: instanced point-cloud render, skipping Alembic.", flush=True)
elif all_positions is not None and bodies:
    for idx, body_def in enumerate(bodies):
        obj = create_anim_mesh(body_def)
        obj.animation_data_create()
        action = bpy.data.actions.new(name=f"{body_def['name']}_Action")
        obj.animation_data.action = action
        _set_fcurves(action, "location", _map_pos_arr(all_positions[:, idx, :].astype(np.float64)))
        if orient is not None:
            _set_fcurves(action, "rotation_quaternion", _map_quat_arr(orient[:, idx, :].astype(np.float64)))
        anim_objects.append(obj)
        if (idx + 1) % 100 == 0 or (idx + 1) == total_bodies:
            print(f"Keyframed {idx + 1}/{total_bodies} bodies", flush=True)

    # Export Alembic (animated objects only). Alembic is time-based, so the
    # scene fps MUST match the render fps here — otherwise the cache spans the
    # wrong number of seconds and re-importing at a different fps silently drops
    # frames (e.g. 30 frames @ 24fps re-read at 15fps becomes ~19 frames).
    bpy.context.scene.render.fps = frame_rate
    for obj in anim_objects:
        obj.select_set(True)
    print("Exporting Alembic... (Blender may be quiet for a while)", flush=True)
    bpy.ops.wm.alembic_export(
        filepath=str(abc_path),
        start=frame_start,
        end=frame_end,
        selected=True,
        visible_objects_only=False,
        flatten=False,
        export_hair=False,
        export_particles=False,
        export_custom_properties=False,
    )
    print(f"Alembic written to {abc_path}", flush=True)
else:
    if not abc_path.exists():
        print(f"No body definitions found and missing Alembic cache at {abc_path}", flush=True)
        sys.exit(1)
    print(f"Skipping Alembic export; using existing cache at {abc_path}", flush=True)

scene = bpy.context.scene
scene.frame_start = frame_start
scene.frame_end = frame_end
scene.render.fps = frame_rate

# Stage 2: clear, import Alembic, rebuild static elements, render
print("=== Blender render ===", flush=True)
scene_loaded = open_scene_file(scene_path_str)
if not scene_loaded:
    clear_scene()
scene = bpy.context.scene
scene.frame_start = frame_start
scene.frame_end = frame_end
scene.render.fps = frame_rate
scene.unit_settings.scale_length = 1.0
existing_obj_names = set(bpy.data.objects.keys())
scene_prepped = False
scene_alembic_path = None
if scene_loaded:
    scene_prepped = bool(scene.get("scene_has_alembic", False))
    scene_alembic_path = scene.get("scene_alembic_path")
skip_alembic_import = False
if scene_prepped and scene_alembic_path:
    if scene_alembic_path == str(abc_path):
        skip_alembic_import = True
    else:
        print(
            f"Scene Alembic path {scene_alembic_path} does not match {abc_path}; re-importing.",
            flush=True,
        )

imported_objs = []
imported_obj_map = {}
if PARTICLE_MODE:
    build_particle_instances(bpy.context.scene)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    bpy.context.scene.render.fps = frame_rate
elif not skip_alembic_import:
    # set_frame_range=False: don't let the importer resize the scene range to the
    # cache's time span (which can shrink it on an fps mismatch). We set the
    # range explicitly from the physics frame count.
    bpy.ops.wm.alembic_import(
        filepath=str(abc_path), as_background_job=False, scale=1.0, set_frame_range=False
    )
    imported_objs = [obj for obj in bpy.data.objects if obj.name not in existing_obj_names]
    imported_obj_map = {obj.name: obj for obj in imported_objs}
    # Re-assert the intended range in case the import nudged it anyway.
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    bpy.context.scene.render.fps = frame_rate

# Ensure imported objects are visible for render/viewport
for obj in imported_objs:
    obj.hide_viewport = False
    obj.hide_render = False
    obj.hide_set(False)

def find_imported_body(name):
    obj = imported_obj_map.get(name)
    if obj:
        return obj
    for cand in imported_objs:
        if cand.name.startswith(f"{name}."):
            return cand
    return bpy.data.objects.get(name)

# Apply materials to imported animated objects (particle mode sets its own).
preserve_materials = PRESERVE_MATERIALS or scene_prepped
if not preserve_materials and not PARTICLE_MODE:
    for body_def in bodies:
        obj = find_imported_body(body_def["name"])
        if not obj:
            continue
        mat = bpy.data.materials.new(name=f"{obj.name}_Mat")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        nodes.clear()
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.inputs["Base Color"].default_value = (*body_def["color"], 1.0)
        bsdf.inputs["Roughness"].default_value = 0.45
        out = nodes.new("ShaderNodeOutputMaterial")
        mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
        obj.data.materials.clear()
        obj.data.materials.append(mat)

# Obstacles
obs_meta = metadata.get("obstacles", None)
if obs_meta and not scene_prepped:
    obs_color = (*obs_meta.get("color", (0.25, 0.25, 0.3)), 1)
    count = 0
    for part in obs_meta.get("parts", []):
        if part.get("type") == "box":
            sx, sy, sz = part["size"]
            px, py, pz = part["pos"]
            rot = part.get("rot", [1, 0, 0, 0])
            bpy.ops.mesh.primitive_cube_add(size=1, align='WORLD')
            obs = bpy.context.active_object
            obs.scale = (1.0, 1.0, 1.0)
            obs.dimensions = (sx, sy, sz)
            obs.location = map_pos(mathutils.Vector((px, py, pz)))
            obs.rotation_mode = 'QUATERNION'
            obs.rotation_quaternion = mathutils.Quaternion(rot)
            obs.name = "Obstacle"
            obs_mat = bpy.data.materials.new(name="ObstacleMaterial")
            obs_mat.use_nodes = True
            nodes = obs_mat.node_tree.nodes
            nodes.clear()
            o_bsdf = nodes.new("ShaderNodeBsdfPrincipled")
            o_bsdf.inputs["Base Color"].default_value = obs_color
            o_bsdf.inputs["Roughness"].default_value = 0.6
            o_out = nodes.new("ShaderNodeOutputMaterial")
            obs_mat.node_tree.links.new(o_bsdf.outputs["BSDF"], o_out.inputs["Surface"])
            obs.data.materials.clear()
            obs.data.materials.append(obs_mat)
            count += 1

# Optional environment append from a .blend file
env_world = None
env_collections = []
if env_path_str and not scene_prepped:
    env_path = Path(env_path_str)
    if env_path.exists():
        try:
            with bpy.data.libraries.load(str(env_path), link=False) as (data_from, data_to):
                if data_from.worlds:
                    data_to.worlds = [data_from.worlds[0]]
                if data_from.collections:
                    data_to.collections = [data_from.collections[0]]
            if data_to.worlds:
                env_world = data_to.worlds[0]
            if data_to.collections:
                env_collections = list(data_to.collections)
            print(f"Loaded environment from {env_path}")
        except Exception as exc:
            print(f"Failed to load environment from {env_path}: {exc}")
    else:
        print(f"Environment path not found: {env_path}")

# World background
world = bpy.context.scene.world
if env_world:
    bpy.context.scene.world = env_world
    world = env_world
elif scene_loaded and scene_use_world and world is not None:
    pass
else:
    if world is None:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world
    world.use_nodes = True
    wnodes = world.node_tree.nodes
    wlinks = world.node_tree.links
    wnodes.clear()
    bg = wnodes.new("ShaderNodeBackground")
    bg.inputs[1].default_value = hdri_strength
    bg.inputs[0].default_value = tuple(config.get("world_color", (0.02, 0.02, 0.02, 1.0)))
    out_world = wnodes.new("ShaderNodeOutputWorld")
    wlinks.new(bg.outputs["Background"], out_world.inputs["Surface"])
    if use_hdr and Path(use_hdr).exists():
        env_tex = wnodes.new("ShaderNodeTexEnvironment")
        env_tex.image = bpy.data.images.load(str(use_hdr))
        wlinks.new(env_tex.outputs["Color"], bg.inputs["Color"])

for col in env_collections:
    if col.name not in bpy.context.scene.collection.children.keys():
        bpy.context.scene.collection.children.link(col)

scene_camera = None
if scene_loaded and scene_use_camera:
    scene_camera = find_scene_camera(scene)
    if scene_camera:
        scene.camera = scene_camera

scene_has_any_lights = False
if scene_loaded and scene_use_lights:
    scene_has_any_lights = scene_has_lights(scene)

# Camera — interpret the resolved spec (CAM) into a placement + per-frame move.
if not (scene_loaded and scene_use_camera and scene_camera):
    _move = CAM["move"]
    _mode = _move["mode"]
    _look = CAM["look_at"]
    _base_radius = float(_move.get("radius", 40.0))
    _base_az = float(_move.get("azimuth", 0.0))
    _base_el = float(_move.get("elevation", 12.0))

    # Frame numbers to author over: every simulated frame, so the .blend scrubs
    # correctly even outside the rendered range. Orbit/keyframe timing spans these.
    if frame_numbers is not None:
        _seq = np.asarray(frame_numbers).astype(int)
    else:
        _seq = np.arange(frames_total)
    _f0, _fN = int(_seq[0]), int(_seq[-1])
    _span = max(1, _fN - _f0)

    def _look_target(fnum):
        """Where the camera looks, in Chrono space, for a given frame."""
        if _look == "clump" and clump_positions is not None:
            idx = min(max(int(fnum), 0), clump_positions.shape[0] - 1)
            v = clump_positions[idx]
            return mathutils.Vector((float(v[0]), float(v[1]), float(v[2])))
        if isinstance(_look, (list, tuple)) and len(_look) == 3:
            return mathutils.Vector((float(_look[0]), float(_look[1]), float(_look[2])))
        return mathutils.Vector((0.0, 0.0, 0.0))

    _base_vec = _spherical(_base_radius, _base_az, _base_el)
    # "track": freeze the camera->clump geometry at the first frame and translate
    # with it (constant distance & angle) — matches the prior clump-dolly.
    _track_offset = _base_vec - _look_target(_f0)

    def _cam_position(fnum):
        """Camera position in Chrono space for a frame, per the move mode."""
        if _mode == "static":
            return _base_vec
        if _mode == "track":
            return _look_target(fnum) + _track_offset
        if _mode == "orbit":
            frac = (int(fnum) - _f0) / _span
            sweep = float(_move.get("orbit_degrees", 360.0))
            rad = _lerp(_base_radius, float(_move["radius_to"]), frac) if _move.get("radius_to") is not None else _base_radius
            el = _lerp(_base_el, float(_move["elevation_to"]), frac) if _move.get("elevation_to") is not None else _base_el
            return _look_target(fnum) + _spherical(rad, _base_az + sweep * frac, el)
        if _mode == "keyframes":
            t = int(fnum) / float(frame_rate)
            rad, az, el = _interp_keyframes(_move.get("keyframes", []), t, _base_radius, _base_az, _base_el)
            return _look_target(fnum) + _spherical(rad, az, el)
        return _base_vec

    bpy.ops.object.camera_add(location=map_pos(_cam_position(_f0)))
    camera = bpy.context.active_object
    camera.name = "Camera"
    scene.camera = camera
    bpy.ops.object.empty_add(location=map_pos(_look_target(_f0)))
    target = bpy.context.active_object
    target.name = "CameraTarget"

    # Animate only when the shot actually moves (or follows a moving target);
    # otherwise set a single static transform so the camera stays hand-editable.
    _animated = (_mode != "static") or (_look == "clump")
    if _animated:
        for _i in range(len(_seq)):
            _fn = int(_seq[_i])
            _pos = map_pos(_cam_position(_fn))
            _ctr = map_pos(_look_target(_fn))
            camera.location = _pos
            camera.keyframe_insert(data_path="location", frame=_fn)
            _aim = _ctr - _pos
            if _aim.length > 0:
                camera.rotation_euler = _aim.to_track_quat('-Z', 'Y').to_euler()
            camera.keyframe_insert(data_path="rotation_euler", frame=_fn)
            target.location = _ctr  # DOF focus rides the look-at target
            target.keyframe_insert(data_path="location", frame=_fn)
    else:
        _pos = map_pos(_cam_position(_f0))
        _ctr = map_pos(_look_target(_f0))
        camera.location = _pos
        _aim = _ctr - _pos
        if _aim.length > 0:
            camera.rotation_euler = _aim.to_track_quat('-Z', 'Y').to_euler()
        target.location = _ctr

    camera.data.lens = float(CAM["lens_mm"])
    camera.data.clip_start = 0.1
    # Large far-clip: swarms can expand to many hundreds of units; empty space has
    # no z-fighting concerns, so a generous clip keeps far bodies from vanishing.
    camera.data.clip_end = 5000
    camera.data.dof.focus_object = target
    camera.data.dof.aperture_fstop = float(CAM["fstop"])

# Lights
if not (scene_loaded and scene_use_lights and scene_has_any_lights):
    for cfg in config.get("light_configs", []):
        light_type = cfg.get("type", "POINT")
        pos = cfg.get("pos", (2, -2, 4))
        energy = cfg.get("energy", 50)
        bpy.ops.object.light_add(type=light_type, location=pos)
        light = bpy.context.active_object
        light.data.energy = energy
        size = cfg.get("size")
        if size is not None and hasattr(light.data, "size"):
            light.data.size = size
        # Area/spot/sun emit along local -Z, so aim them at the origin where the
        # bodies live (points are omnidirectional and don't care).
        if light_type in ("AREA", "SPOT", "SUN"):
            direction = mathutils.Vector((0, 0, 0)) - light.location
            if direction.length > 0:
                light.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()

# Origin marker: a small static red cube at world origin to mark the center.
# Purely a render aid — it's never part of the physics sim, so it can't move or
# collide; bodies just pass through it. Skipped if one is already in the scene.
SHOW_ORIGIN = bool(config.get("show_origin_marker", False))
if SHOW_ORIGIN and "OriginMarker" not in bpy.data.objects:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    _marker = bpy.context.active_object
    _marker.name = "OriginMarker"
    _mmat = bpy.data.materials.new(name="OriginMarkerMat")
    _mmat.use_nodes = True
    _mnodes = _mmat.node_tree.nodes
    _mnodes.clear()
    _mbsdf = _mnodes.new("ShaderNodeBsdfPrincipled")
    _mbsdf.inputs["Base Color"].default_value = (1.0, 0.04, 0.04, 1.0)
    # Slight self-illumination so it reads as a clear red marker in any lighting.
    if "Emission Color" in _mbsdf.inputs:
        _mbsdf.inputs["Emission Color"].default_value = (1.0, 0.04, 0.04, 1.0)
    if "Emission Strength" in _mbsdf.inputs:
        _mbsdf.inputs["Emission Strength"].default_value = 1.0
    _mout = _mnodes.new("ShaderNodeOutputMaterial")
    _mmat.node_tree.links.new(_mbsdf.outputs["BSDF"], _mout.inputs["Surface"])
    _marker.data.materials.append(_mmat)

# Render settings
scene.render.engine = 'CYCLES'
enable_devices()
scene.cycles.samples = preset["samples"]
scene.cycles.use_denoising = bool(preset["denoise"])
# Fast GPU denoiser when denoising is on; fall back to OpenImageDenoise (CPU-ok).
try:
    scene.cycles.denoiser = 'OPTIX'
except Exception:
    try:
        scene.cycles.denoiser = 'OPENIMAGEDENOISE'
    except Exception:
        pass
# --- Animation speedups (apply to every preset) ---
# 1. Keep the scene/BVH resident between frames — only transforms change, so
#    re-syncing geometry every frame is pure waste.
scene.render.use_persistent_data = True
# 2. Stop sampling pixels that have already converged (huge for empty space).
scene.cycles.use_adaptive_sampling = True
scene.cycles.adaptive_threshold = 0.01
# 3. Opaque bodies in a void don't need deep light paths.
scene.cycles.max_bounces = 4
scene.cycles.diffuse_bounces = 2
scene.cycles.glossy_bounces = 2
scene.cycles.transmission_bounces = 2
scene.cycles.volume_bounces = 0
scene.render.fps = frame_rate
scene.render.resolution_x = preset["res_x"]
scene.render.resolution_y = preset["res_y"]
scene.view_layers[0].use_pass_ambient_occlusion = True
scene.display_settings.display_device = 'sRGB'
scene.view_settings.view_transform = 'Standard'
scene.view_settings.look = 'None'
scene.view_settings.exposure = 0.0
scene.view_settings.gamma = 1.0
scene.render.use_motion_blur = False
scene.render.filepath = str(render_dir / "frame_")
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGB'
scene.render.image_settings.color_depth = '8'

scene_output_path = resolve_scene_output_path(scene_output_path_str)
if scene_output_path:
    scene["scene_has_alembic"] = True
    scene["scene_alembic_path"] = str(abc_path)
    bpy.ops.wm.save_as_mainfile(filepath=str(scene_output_path))
    print(f"Saved Blender scene to {scene_output_path}", flush=True)
    if STOP_AFTER_SCENE:
        print("Stopping before render (scene export only).", flush=True)
        sys.exit(0)

if FIRST_FRAME_ONLY:
    scene.frame_start = frame_start
    scene.frame_end = frame_start
    scene.frame_set(frame_start)
    # Write into the run's own rendered_frames/ so it shows in the gallery
    # (and so the lighting-preview loop has a per-run artifact to serve).
    rendered = render_dir / f"frame_{frame_start:04d}.png"
    scene.render.filepath = str(rendered)
    print(f"Rendering single frame to {rendered}", flush=True)
    bpy.ops.render.render(write_still=True)
    # Keep the shared convenience copies the CLI relies on (index.py m).
    if rendered.exists():
        shutil.copyfile(rendered, base_output / "first_frame.png")
        shutil.copyfile(rendered, base_output / "most_recent_frame.png")
    print("Single-frame render complete.", flush=True)
else:
    total_frames = frame_end - frame_start + 1
    state = {"done": 0}
    def render_write_callback(scene, depsgraph):
        state["done"] += 1
        print(f"Rendering frames: {state['done']}/{total_frames}", flush=True)
        src = Path(f"{scene.render.filepath}{scene.frame_current:04d}.png")
        if src.exists():
            first_dest = base_output / "first_frame.png"
            recent_dest = base_output / "most_recent_frame.png"
            if state["done"] == 1:
                shutil.copyfile(src, first_dest)
                print(f"Copied first frame to {first_dest}")
            shutil.copyfile(src, recent_dest)
        if state["done"] == total_frames:
            print("Rendering frames: done", flush=True)
    bpy.app.handlers.render_write.clear()
    bpy.app.handlers.render_write.append(render_write_callback)
    print(f"Rendering frames to {render_dir}", flush=True)
    bpy.ops.render.render(animation=True)
    print("Render complete.", flush=True)
    # Fallback: ensure first/most-recent copies are written to the output root.
    if render_dir.exists():
        frames = sorted(render_dir.glob("frame_*.png"))
        if frames:
            dest_first = base_output / "first_frame.png"
            dest_recent = base_output / "most_recent_frame.png"
            shutil.copyfile(frames[0], dest_first)
            shutil.copyfile(frames[-1], dest_recent)
            print(f"Synced first/most-recent frames to {base_output}", flush=True)
