import bpy
import math
import sys

# ---- Configurable Variables ----
QUALITY = "low"  # "low" or "high" (override with -- high)
RESOLUTION_X = 960  # Low: 960, High: 1920
RESOLUTION_Y = 540  # Low: 540, High: 1080
FPS = 10            # Low: 10, High: 60
FRAME_END = 150     # Low: 150 (15 sec), High: 300
SAMPLES = 32        # Low: 32, High: 128
ROCK_MASS = 10.0    # Total rock mass
SHATTER_THRESHOLD = 50  # Force to start fracturing (tweak 10-100)
CAMERA_POS = (20, -20, 10)
CAMERA_ROT = (math.radians(60), 0, math.radians(45))
LIGHT_POS = (5, -5, 15)
LIGHT_COLOR = (1.0, 0.9, 0.7)
ROCK_COLOR = (0.6, 0.3, 0.1, 1)

# Override quality from command line
if len(sys.argv) > 4 and sys.argv[4] == "high":
    QUALITY = "high"
    RESOLUTION_X = 1920
    RESOLUTION_Y = 1080
    FPS = 60
    FRAME_END = 300
    SAMPLES = 128

# ---- Scene Setup ----
bpy.ops.object.select_all(action='DESELECT')
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

scene = bpy.context.scene
scene.render.engine = 'CYCLES' if QUALITY == "high" else 'BLENDER_EEVEE_NEXT'
scene.render.resolution_x = RESOLUTION_X
scene.render.resolution_y = RESOLUTION_Y
scene.render.fps = FPS
scene.frame_end = FRAME_END
scene.render.filepath = "/home/matt/Projects/space/rock_shatter.mp4"
scene.render.image_settings.file_format = 'FFMPEG'
scene.render.ffmpeg.format = 'MPEG4'
scene.render.ffmpeg.codec = 'H264'
scene.render.ffmpeg.constant_rate_factor = 'PERC_LOSSLESS' if QUALITY == "high" else 'MEDIUM'
scene.render.use_motion_blur = True
scene.cycles.samples = SAMPLES
scene.eevee.taa_render_samples = SAMPLES

# Ground
bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
ground = bpy.context.active_object
ground.name = "Ground"
bpy.context.view_layer.objects.active = ground
bpy.ops.object.mode_set(mode='OBJECT')
bpy.ops.rigidbody.object_add()
ground.rigid_body.type = 'PASSIVE'
ground.rigid_body.friction = 0.8
ground.rigid_body.restitution = 0.1
ground.select_set(False)

# Rock
bpy.ops.mesh.primitive_ico_sphere_add(radius=1, subdivisions=4, location=(0, 0, 5))
rock = bpy.context.active_object
rock.name = "Rock"
bpy.context.view_layer.objects.active = rock
bpy.ops.object.mode_set(mode='OBJECT')
bpy.ops.rigidbody.object_add()
rock.rigid_body.type = 'ACTIVE'
rock.rigid_body.mass = ROCK_MASS
bpy.ops.object.modifier_add(type='SUBSURF')
rock.modifiers["Subdivision"].levels = 2
bpy.ops.object.modifier_apply(modifier="Subdivision")

# Material
rock_mat = bpy.data.materials.new(name="RockMaterial")
rock_mat.use_nodes = True
nodes = rock_mat.node_tree.nodes
nodes.clear()
principled = nodes.new('ShaderNodeBsdfPrincipled')
principled.inputs['Base Color'].default_value = ROCK_COLOR
principled.inputs['Roughness'].default_value = 0.9
principled.inputs['Metallic'].default_value = 0.2
output = nodes.new('ShaderNodeOutputMaterial')
rock_mat.node_tree.links.new(principled.outputs['BSDF'], output.inputs['Surface'])
rock.data.materials.append(rock_mat)

# Dynamic Fracture (Fracture Modifier)
bpy.ops.object.modifier_add(type='FRACTURE')
fm = rock.modifiers[-1]
fm.fracture_mode = 'DYNAMIC'  # Real-time fracturing
fm.shard_count = 50  # Max pieces (tweakable)
fm.break_threshold = SHATTER_THRESHOLD  # Force to break (lower = easier)
fm.use_mass = True  # Mass affects fracture
fm.fracture_cell_size = 0.1  # Smaller = finer shards
fm.use_smooth = True  # Smoother edges

# Camera
bpy.ops.object.camera_add(location=CAMERA_POS, rotation=CAMERA_ROT)
scene.camera = bpy.context.active_object

# Light
bpy.ops.object.light_add(type='SUN', location=LIGHT_POS)
light = bpy.context.active_object
light.data.energy = 12
light.data.color = LIGHT_COLOR

# Bake and render
scene.rigid_body_world.point_cache.frame_end = FRAME_END
bpy.ops.ptcache.free_bake_all()
bpy.ops.ptcache.bake_all(bake=True)
bpy.ops.render.render(animation=True)

print("Done! Check /home/matt/Projects/space/rock_shatter.mp4")