import bpy
from pathlib import Path
import random

# Fresh scene
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

# Color management: Standard, no extra exposure
scene.display_settings.display_device = "sRGB"
scene.view_settings.view_transform = "Standard"
scene.view_settings.exposure = 0.0

# Render settings
scene.render.engine = "CYCLES"
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGB"
scene.render.image_settings.color_depth = "8"
scene.render.resolution_x = 800
scene.render.resolution_y = 800
scene.cycles.samples = 32
scene.cycles.use_denoising = False

# Background
world = bpy.data.worlds.new("World")
scene.world = world
world.use_nodes = True
wnodes = world.node_tree.nodes
wlinks = world.node_tree.links
wnodes.clear()
bg = wnodes.new("ShaderNodeBackground")
bg.inputs["Color"].default_value = (0.02, 0.02, 0.02, 1.0)
bg.inputs["Strength"].default_value = 1.0
out_world = wnodes.new("ShaderNodeOutputWorld")
wlinks.new(bg.outputs["Background"], out_world.inputs["Surface"])

# Light
bpy.ops.object.light_add(type="POINT", location=(2, -2, 4))
light = bpy.context.active_object
light.data.energy = 50

# Cube with Principled diffuse color
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
cube = bpy.context.active_object
mat = bpy.data.materials.new(name="TestColor")
mat.use_nodes = True
nodes = mat.node_tree.nodes
links = mat.node_tree.links
nodes.clear()
bsdf = nodes.new("ShaderNodeBsdfPrincipled")
color = (random.random(), random.random(), random.random(), 1.0)
bsdf.inputs["Base Color"].default_value = color
bsdf.inputs["Roughness"].default_value = 0.45
out_mat = nodes.new("ShaderNodeOutputMaterial")
links.new(bsdf.outputs["BSDF"], out_mat.inputs["Surface"])
cube.data.materials.clear()
cube.data.materials.append(mat)

# Camera
bpy.ops.object.camera_add(location=(0, -6, 2), rotation=(1.25, 0, 0))
scene.camera = bpy.context.active_object

# Render
out_dir = Path("output")
out_dir.mkdir(parents=True, exist_ok=True)
out_path = out_dir / "color_test.png"
scene.render.filepath = str(out_path)
# Render to disk
bpy.ops.render.render(write_still=True)

# Inspect pixel stats from the saved file to avoid empty render slots
img = bpy.data.images.load(str(out_path))
pixels = list(img.pixels)  # RGBA flattened
rs = [pixels[i] for i in range(0, len(pixels), 4)]
gs = [pixels[i] for i in range(1, len(pixels), 4)]
bs = [pixels[i] for i in range(2, len(pixels), 4)]
print(f"Rendered test image → {out_path} with color={color}")
print(f"Pixel stats: R min/max {min(rs):.3f}/{max(rs):.3f} G {min(gs):.3f}/{max(gs):.3f} B {min(bs):.3f}/{max(bs):.3f}")
