import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


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


def write_blender_driver(
    run_dir: Path,
    npz_path: Path,
    metadata: dict,
    quality: str,
    frame_rate: int,
    first_frame_only: bool,
    resume_frame: int = 0,
    config: Optional[dict] = None,
) -> Path:
    cfg_data = config or {}
    presets = cfg_data.get("quality_presets", {})
    preset = presets.get(quality, presets.get("high", {}))
    if not preset:
        raise ValueError(f"Quality preset '{quality}' not found in config.")
    script_path = run_dir / "blender_stage.py"
    alembic_dir = run_dir / "alembic"
    render_dir = run_dir / "rendered_frames"
    physics_dir = run_dir / "physics"
    script_path.write_text(
        f"""
import math
import os
import sys
import shutil
from pathlib import Path

import bpy
import mathutils
import numpy as np
import json

npy_path = Path(r"{npz_path}")
run_dir = Path(r"{run_dir}")
physics_dir = run_dir / "physics"
alembic_dir = run_dir / "alembic"
render_dir = run_dir / "rendered_frames"
abc_path = alembic_dir / "motion_data.abc"
frame_rate = {frame_rate}
base_output = run_dir.parent
metadata = json.loads((run_dir / "run_metadata.json").read_text())
use_hdr = os.getenv("HDRI_PATH")
FIRST_FRAME_ONLY = {str(first_frame_only)}
frame_start = {resume_frame}

physics_dir.mkdir(parents=True, exist_ok=True)
alembic_dir.mkdir(parents=True, exist_ok=True)
render_dir.mkdir(parents=True, exist_ok=True)


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def enable_devices():
    prefs = bpy.context.preferences.addons['cycles'].preferences
    prefs.get_devices()
    preferred = ("OPTIX", "CUDA", "HIP", "METAL")
    chosen = None
    for dev_type in preferred:
        prefs.compute_device_type = dev_type
        prefs.get_devices()
        if any(d.type == dev_type for d in prefs.devices):
            chosen = dev_type
            break
    if chosen:
        for device in prefs.devices:
            device.use = device.type == chosen
        bpy.context.scene.cycles.device = 'GPU'
        # Using GPU
    else:
        bpy.context.scene.cycles.device = 'CPU'
        print("Falling back to CPU rendering.")


def map_pos(vec):
    rot = mathutils.Quaternion((1.0, 0.0, 0.0), math.pi / 2.0)
    return rot @ vec


def map_quat(quat):
    rot = mathutils.Quaternion((1.0, 0.0, 0.0), math.pi / 2.0)
    return rot @ quat @ rot.conjugated()


# Stage 1: load NPZ, build animation, export Alembic (animated bodies only)
print("=== Alembic export ===", flush=True)
clear_scene()
print("Loading physics NPZ...", flush=True)
data = np.load(str(npy_path))
frames_total = metadata.get("frames", 0)
# `frames` in metadata is a count; last keyed frame index is count-1.
frame_end = max(frame_start, frames_total - 1)
if frame_start > frame_end:
    print(f"Nothing to render: start frame {{frame_start}} exceeds last frame {{frame_end}}")
    sys.exit(0)
total_bodies = len(metadata.get("bodies", []))
print(f"Loaded physics data for {{total_bodies}} bodies, {{frames_total}} frames", flush=True)

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
        raise ValueError(f"Unknown shape type: {{shape}}")

    obj.name = body_def["name"]
    obj.rotation_mode = 'QUATERNION'
    return obj

def _fill_curve(fcurve, frames, values):
    kps = fcurve.keyframe_points
    kps.add(len(frames))
    for i, (f, v) in enumerate(zip(frames, values)):
        kp = kps[i]
        kp.co = (float(f), float(v))
        kp.interpolation = 'LINEAR'
    kps.update()


anim_objects = []
for idx, body_def in enumerate(metadata["bodies"]):
    obj = create_anim_mesh(body_def)
    obj.animation_data_create()
    action = bpy.data.actions.new(name=f"{{body_def['name']}}_Action")
    obj.animation_data.action = action
    loc_x = action.fcurves.new(data_path="location", index=0)
    loc_y = action.fcurves.new(data_path="location", index=1)
    loc_z = action.fcurves.new(data_path="location", index=2)
    rot_w = action.fcurves.new(data_path="rotation_quaternion", index=0)
    rot_x = action.fcurves.new(data_path="rotation_quaternion", index=1)
    rot_y = action.fcurves.new(data_path="rotation_quaternion", index=2)
    rot_z = action.fcurves.new(data_path="rotation_quaternion", index=3)
    arr = data[body_def["name"]]
    frames = []
    lx = []
    ly = []
    lz = []
    qw_list = []
    qx_list = []
    qy_list = []
    qz_list = []
    for frame, _, x, y, z, qw, qx, qy, qz in arr:
        frame = int(frame)
        pos_vec = map_pos(mathutils.Vector((x, y, z)))
        quat = map_quat(mathutils.Quaternion((qw, qx, qy, qz)))
        frames.append(frame)
        lx.append(pos_vec.x)
        ly.append(pos_vec.y)
        lz.append(pos_vec.z)
        qw_list.append(quat.w)
        qx_list.append(quat.x)
        qy_list.append(quat.y)
        qz_list.append(quat.z)
    _fill_curve(loc_x, frames, lx)
    _fill_curve(loc_y, frames, ly)
    _fill_curve(loc_z, frames, lz)
    _fill_curve(rot_w, frames, qw_list)
    _fill_curve(rot_x, frames, qx_list)
    _fill_curve(rot_y, frames, qy_list)
    _fill_curve(rot_z, frames, qz_list)
    anim_objects.append(obj)
    print(f"Keyframed {{idx + 1}}/{{total_bodies}} bodies", flush=True)

scene = bpy.context.scene
scene.frame_start = frame_start
scene.frame_end = frame_end

# Export Alembic (animated objects only)
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
print(f"Alembic written to {{abc_path}}", flush=True)

# Stage 2: clear, import Alembic, rebuild static elements, render
print("=== Blender render ===", flush=True)
clear_scene()
scene = bpy.context.scene
scene.frame_start = frame_start
scene.frame_end = frame_end
scene.unit_settings.scale_length = 1.0
bpy.ops.wm.alembic_import(filepath=str(abc_path), as_background_job=False, scale=1.0)

# Ensure imported objects are visible for render/viewport
for obj in bpy.context.scene.objects:
    obj.hide_viewport = False
    obj.hide_render = False
    obj.hide_set(False)

# Apply materials to imported animated objects
for body_def in metadata["bodies"]:
    obj = bpy.data.objects.get(body_def["name"])
    if not obj:
        continue
    mat = bpy.data.materials.new(name=f"{{obj.name}}_Mat")
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
if obs_meta:
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

# World background
world = bpy.context.scene.world
if world is None:
    world = bpy.data.worlds.new("World")
    bpy.context.scene.world = world
world.use_nodes = True
wnodes = world.node_tree.nodes
wlinks = world.node_tree.links
wnodes.clear()
bg = wnodes.new("ShaderNodeBackground")
bg.inputs[1].default_value = 1.0
bg.inputs[0].default_value = (0.02, 0.02, 0.02, 1)
out_world = wnodes.new("ShaderNodeOutputWorld")
wlinks.new(bg.outputs["Background"], out_world.inputs["Surface"])
if use_hdr and Path(use_hdr).exists():
    env_tex = wnodes.new("ShaderNodeTexEnvironment")
    env_tex.image = bpy.data.images.load(str(use_hdr))
    wlinks.new(env_tex.outputs["Color"], bg.inputs["Color"])

# Camera
camera_radius = {cfg_data.get("camera_radius", 40.0)}
cam_pos = map_pos(mathutils.Vector((camera_radius, 0, 0)))
target_pos = map_pos(mathutils.Vector((0, 0, 0)))
bpy.ops.object.camera_add(location=cam_pos)
camera = bpy.context.active_object
camera.name = "Camera"
scene.camera = camera
bpy.ops.object.empty_add(location=target_pos)
target = bpy.context.active_object
target.name = "CameraTarget"
constraint = camera.constraints.new(type='TRACK_TO')
constraint.target = target
constraint.track_axis = 'TRACK_NEGATIVE_Z'
constraint.up_axis = 'UP_Y'
camera.data.lens = 35
camera.data.clip_start = 0.1
camera.data.clip_end = 100
camera.data.dof.focus_object = target
camera.data.dof.aperture_fstop = 4.0

# Lights
for cfg in {cfg_data.get("light_configs", [])}:
    light_type = cfg.get("type", "POINT")
    pos = cfg.get("pos", (2, -2, 4))
    energy = cfg.get("energy", 50)
    bpy.ops.object.light_add(type=light_type, location=pos)
    light = bpy.context.active_object
    light.data.energy = energy

# Render settings
scene.render.engine = 'CYCLES'
enable_devices()
scene.cycles.samples = {preset["samples"]}
scene.cycles.use_denoising = {str(preset["denoise"]).capitalize()}
scene.render.fps = frame_rate
scene.render.resolution_x = {preset["res_x"]}
scene.render.resolution_y = {preset["res_y"]}
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

if FIRST_FRAME_ONLY:
    scene.frame_start = frame_start
    scene.frame_end = frame_start
    scene.render.filepath = str(base_output / "first_frame.png")
    print(f"Rendering single frame to {{scene.render.filepath}}", flush=True)
    bpy.ops.render.render(write_still=True)
    mr_dest = base_output / "most_recent_frame.png"
    shutil.copyfile(scene.render.filepath, mr_dest)
    print(f"Copied most recent frame to {{mr_dest}}", flush=True)
    print("Single-frame render complete.", flush=True)
else:
    total_frames = frame_end - frame_start + 1
    state = {{"done": 0}}
    def render_write_callback(scene, depsgraph):
        state["done"] += 1
        print(f"Rendering frames: {{state['done']}}/{{total_frames}}", flush=True)
        src = Path(f"{{scene.render.filepath}}{{scene.frame_current:04d}}.png")
        if src.exists():
            first_dest = base_output / "first_frame.png"
            recent_dest = base_output / "most_recent_frame.png"
            if state["done"] == 1:
                shutil.copyfile(src, first_dest)
                print(f"Copied first frame to {{first_dest}}")
            shutil.copyfile(src, recent_dest)
        if state["done"] == total_frames:
            print("Rendering frames: done", flush=True)
    bpy.app.handlers.render_write.clear()
    bpy.app.handlers.render_write.append(render_write_callback)
    print(f"Rendering frames to {{render_dir}}", flush=True)
    bpy.ops.render.render(animation=True)
    print("Render complete.", flush=True)
"""
    )
    return script_path


def run_blender(blender_bin: str, script_path: Path, run_dir: Path, logger) -> None:
    log_path = run_dir / "blender.log"
    cmd = [blender_bin, "-b", "-P", str(script_path)]
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
