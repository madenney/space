import bpy
import math
import os
import random

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
scene.frame_end = 120  # Total frames for animation

# Render settings
scene.render.engine = 'BLENDER_EEVEE_NEXT'
scene.render.resolution_x = 1920
scene.render.resolution_y = 1080
scene.render.resolution_percentage = 50
scene.render.filepath = os.path.join(output_dir, "frame_")
scene.render.image_settings.file_format = 'PNG'

# Function to add a sphere and animate it with random velocity
def add_sphere(name, location, velocity):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=location)
    sphere = bpy.context.object
    sphere.name = name

    # Animate position over time based on velocity
    start_frame = 10
    end_frame = scene.frame_end

    # Initial position
    sphere.location = location
    sphere.keyframe_insert(data_path="location", frame=start_frame)

    # End position based on velocity (velocity * time)
    end_location = (
        location[0] + velocity[0] * (end_frame - start_frame),
        location[1] + velocity[1] * (end_frame - start_frame),
        location[2] + velocity[2] * (end_frame - start_frame),
    )
    sphere.location = end_location
    sphere.keyframe_insert(data_path="location", frame=end_frame)

# Add two spheres with random velocity
random_velocity = lambda: random.uniform(-0.2, 0.2)  # Small velocity for "space" motion
add_sphere("Sphere_Left", location=(-2, 0, 0), velocity=(random_velocity(), random_velocity(), random_velocity()))
add_sphere("Sphere_Right", location=(2, 0, 0), velocity=(random_velocity(), random_velocity(), random_velocity()))

# Set up the camera
bpy.ops.object.camera_add(location=(10, -10, 8), rotation=(math.radians(60), 0, math.radians(45)))
scene.camera = bpy.context.object

# Add lighting to simulate a "space" environment
bpy.ops.object.light_add(type='SUN', location=(10, -10, 10))
bpy.context.object.data.energy = 2  # Slightly brighter light for space

# Render the animation to PNG frames
bpy.ops.render.render(animation=True)

# Combine frames into a video using Blender's FFmpeg
scene.render.filepath = os.path.join(output_dir, "space_simulation.mp4")
scene.render.image_settings.file_format = 'FFMPEG'
scene.render.ffmpeg.format = 'MPEG4'
scene.render.ffmpeg.codec = 'H264'
scene.render.ffmpeg.constant_rate_factor = "MEDIUM"

bpy.ops.render.render(animation=True)

print(f"Animation complete. Output saved to: {output_dir}")