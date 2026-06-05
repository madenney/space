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
    scene_path: Optional[Path] = None,
    scene_output_path: Optional[Path] = None,
    stop_after_scene: Optional[bool] = None,
    preserve_materials: Optional[bool] = None,
) -> Path:
    cfg_data = config or {}
    presets = cfg_data.get("quality_presets", {})
    preset = presets.get(quality, presets.get("high", {}))
    if not preset:
        raise ValueError(f"Quality preset '{quality}' not found in config.")
    if scene_path is not None:
        scene_path_value = str(scene_path)
    else:
        scene_path_value = cfg_data.get("blender_scene") or ""
    if scene_output_path is not None:
        scene_output_value = str(scene_output_path)
    else:
        scene_output_value = cfg_data.get("blender_scene_output") or ""
    stop_after_scene_value = (
        stop_after_scene if stop_after_scene is not None else bool(cfg_data.get("blender_stop_after_scene", False))
    )
    preserve_materials_value = (
        preserve_materials if preserve_materials is not None else bool(cfg_data.get("blender_preserve_materials", False))
    )
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
hdri_path_cfg = r"{cfg_data.get('hdri_path') or ''}"
use_hdr = os.getenv("HDRI_PATH") or (hdri_path_cfg if hdri_path_cfg else None)
hdri_strength = {cfg_data.get("hdri_strength", 1.0)}
env_path_str = r"{cfg_data.get('blender_environment') or ''}"
scene_path_str = r"{scene_path_value}"
scene_use_camera = {str(cfg_data.get("blender_scene_use_camera", True)).capitalize()}
scene_use_lights = {str(cfg_data.get("blender_scene_use_lights", True)).capitalize()}
scene_use_world = {str(cfg_data.get("blender_scene_use_world", True)).capitalize()}
scene_output_path_str = r"{scene_output_value}"
STOP_AFTER_SCENE = {str(stop_after_scene_value).capitalize()}
PRESERVE_MATERIALS = {str(preserve_materials_value).capitalize()}
FIRST_FRAME_ONLY = {str(first_frame_only)}
frame_start = {resume_frame}

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
        print(f"Scene path not found: {{scene_path}}")
        return False
    try:
        bpy.ops.wm.open_mainfile(filepath=str(scene_path), load_ui=False)
        print(f"Loaded scene from {{scene_path}}", flush=True)
        return True
    except Exception as exc:
        print(f"Failed to load scene {{scene_path}}: {{exc}}")
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
        valid_types = {{item.identifier for item in prefs.bl_rna.properties['compute_device_type'].enum_items}}
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
        print(f"Using GPU backend: {{chosen}}", flush=True)
    else:
        bpy.context.scene.cycles.device = 'CPU'
        print("Falling back to CPU rendering.", flush=True)


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
data_keys = list(data.files)
bodies = metadata.get("bodies") or []
frames_total = 0
if data_keys:
    frames_total = int(data[data_keys[0]].shape[0])
else:
    frames_total = int(metadata.get("frames", 0) or 0)
# `frames` in metadata is a count; last keyed frame index is count-1.
frame_end = max(frame_start, frames_total - 1)
if frame_start > frame_end:
    print(f"Nothing to render: start frame {{frame_start}} exceeds last frame {{frame_end}}")
    sys.exit(0)
total_bodies = len(bodies) if bodies else len(data_keys)
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
if bodies:
    for idx, body_def in enumerate(bodies):
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
else:
    if not abc_path.exists():
        print(f"No body definitions found and missing Alembic cache at {{abc_path}}", flush=True)
        sys.exit(1)
    print(f"Skipping Alembic export; using existing cache at {{abc_path}}", flush=True)

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
            f"Scene Alembic path {{scene_alembic_path}} does not match {{abc_path}}; re-importing.",
            flush=True,
        )

imported_objs = []
imported_obj_map = {{}}
if not skip_alembic_import:
    bpy.ops.wm.alembic_import(filepath=str(abc_path), as_background_job=False, scale=1.0)
    imported_objs = [obj for obj in bpy.data.objects if obj.name not in existing_obj_names]
    imported_obj_map = {{obj.name: obj for obj in imported_objs}}

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
        if cand.name.startswith(f"{{name}}."):
            return cand
    return bpy.data.objects.get(name)

# Apply materials to imported animated objects
preserve_materials = PRESERVE_MATERIALS or scene_prepped
if not preserve_materials:
    for body_def in bodies:
        obj = find_imported_body(body_def["name"])
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
            print(f"Loaded environment from {{env_path}}")
        except Exception as exc:
            print(f"Failed to load environment from {{env_path}}: {{exc}}")
    else:
        print(f"Environment path not found: {{env_path}}")

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
    bg.inputs[0].default_value = (0.02, 0.02, 0.02, 1)
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

# Camera
if not (scene_loaded and scene_use_camera and scene_camera):
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
if not (scene_loaded and scene_use_lights and scene_has_any_lights):
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

scene_output_path = resolve_scene_output_path(scene_output_path_str)
if scene_output_path:
    scene["scene_has_alembic"] = True
    scene["scene_alembic_path"] = str(abc_path)
    bpy.ops.wm.save_as_mainfile(filepath=str(scene_output_path))
    print(f"Saved Blender scene to {{scene_output_path}}", flush=True)
    if STOP_AFTER_SCENE:
        print("Stopping before render (scene export only).", flush=True)
        sys.exit(0)

if FIRST_FRAME_ONLY:
    scene.frame_start = frame_start
    scene.frame_end = frame_start
    scene.frame_set(frame_start)
    # Write into the run's own rendered_frames/ so it shows in the gallery
    # (and so the lighting-preview loop has a per-run artifact to serve).
    rendered = render_dir / f"frame_{{frame_start:04d}}.png"
    scene.render.filepath = str(rendered)
    print(f"Rendering single frame to {{rendered}}", flush=True)
    bpy.ops.render.render(write_still=True)
    # Keep the shared convenience copies the CLI relies on (index.py m).
    if rendered.exists():
        shutil.copyfile(rendered, base_output / "first_frame.png")
        shutil.copyfile(rendered, base_output / "most_recent_frame.png")
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
    # Fallback: ensure first/most-recent copies are written to the output root.
    if render_dir.exists():
        frames = sorted(render_dir.glob("frame_*.png"))
        if frames:
            dest_first = base_output / "first_frame.png"
            dest_recent = base_output / "most_recent_frame.png"
            shutil.copyfile(frames[0], dest_first)
            shutil.copyfile(frames[-1], dest_recent)
            print(f"Synced first/most-recent frames to {{base_output}}", flush=True)
"""
    )
    return script_path


def run_blender(blender_bin: str, script_path: Path, run_dir: Path, logger) -> None:
    log_path = run_dir / "blender.log"
    # --python-exit-code makes Blender propagate Python script errors to its
    # exit status (otherwise it exits 0 even when the -P script raises).
    cmd = [blender_bin, "-b", "--python-exit-code", "1", "-P", str(script_path)]
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
