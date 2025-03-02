import bpy
import math
import os

# Function to create an incremented output folder
def create_output_folder(base_folder="output"):
    current_dir = os.getcwd()  # Get the directory where the script was called
    output_base = os.path.join(current_dir, base_folder)

    # Ensure the base "output" folder exists
    os.makedirs(output_base, exist_ok=True)

    # Increment folder name (output_1, output_2, etc.)
    index = 1
    while True:
        output_dir = os.path.join(output_base, f"output_{index}")
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)  # Create the folder
            return output_dir
        index += 1

# Set up the output directory
output_dir = create_output_folder()
print(f"Output directory: {output_dir}")

# Clear the scene
bpy.ops.wm.read_factory_settings(use_empty=True)

# Set up the scene
scene = bpy.context.scene
scene.frame_start = 1
scene.frame_end = 30  # Total frames for animation

# Render settings: Optimized for speed
scene.render.engine = 'BLENDER_EEVEE_NEXT'  # Use Eevee-Next for fast rendering
scene.render.resolution_x = 1920
scene.render.resolution_y = 1080
scene.render.resolution_percentage = 50  # Render at 50% scale (960x540)

# Render frame path
frame_path = os.path.join(output_dir, "ball_drop_frame_")
scene.render.filepath = frame_path
scene.render.image_settings.file_format = 'PNG'  # Save as PNG images

# Add a ground plane
bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, 0))
ground = bpy.context.object
ground.name = "Ground"

# Add rigid body physics to the ground
bpy.ops.rigidbody.object_add()
ground.rigid_body.type = 'PASSIVE'  # Static ground

# Add a sphere (the ball)
bpy.ops.mesh.primitive_uv_sphere_add(radius=1, location=(0, 0, 5))  # Position sphere above the ground
ball = bpy.context.object
ball.name = "Ball"

# Add rigid body physics to the ball
bpy.ops.rigidbody.object_add()
ball.rigid_body.type = 'ACTIVE'  # Active: the ball will be affected by physics
ball.rigid_body.mass = 1.0       # Set the ball's mass

# Set up the camera
bpy.ops.object.camera_add(location=(10, -10, 8), rotation=(math.radians(60), 0, math.radians(45)))
scene.camera = bpy.context.object

# Add lighting
bpy.ops.object.light_add(type='SUN', location=(10, -10, 10))

# Bake the physics simulation
bpy.ops.ptcache.bake_all(bake=True)

# Render the animation to PNG frames
bpy.ops.render.render(animation=True)

# Combine frames into a video using Blender's FFmpeg
video_path = os.path.join(output_dir, "ball_drop_simulation.mp4")
scene.render.filepath = video_path
scene.render.image_settings.file_format = 'FFMPEG'
scene.render.ffmpeg.format = 'MPEG4'
scene.render.ffmpeg.codec = 'H264'
scene.render.ffmpeg.constant_rate_factor = "MEDIUM"  # Adjust quality

bpy.ops.render.render(animation=True)

print(f"Animation complete. Video saved to: {video_path}")