import unreal
import sys
print("Imports completed successfully")
# ---- Configurable Variables ----
QUALITY = "low"  # "low" or "high" (override with --high)
RESOLUTION_X = 960  # Low: 960, High: 1920
RESOLUTION_Y = 540  # Low: 540, High: 1080
FPS = 24            # More cinematic FPS (Low: 24, High: 60)
FRAME_END = 360     # 15 sec at 24 FPS, 6 sec at 60 FPS
ROCK_MASS = 50.0    # Heavier rock for realistic impact
FRACTURE_THRESHOLD = 75  # Higher threshold for controlled shattering
ROCK_LOCATION = unreal.Vector(0, 0, 1000)  # Higher drop for dramatic effect
CAMERA_POS = unreal.Vector(1500, -1500, 800)  # Closer, angled view
CAMERA_ROT = unreal.Rotator(30, 0, 45)  # Better angle for visibility
LIGHT_POS = unreal.Vector(1000, -1000, 2000)  # Softer, higher light
LIGHT_COLOR = unreal.LinearColor(1.0, 0.95, 0.85, 1.0)  # Warm, natural light
ROCK_COLOR = unreal.LinearColor(0.5, 0.35, 0.25, 1.0)  # Earthy brown

# Override quality from command line
if len(sys.argv) > 1 and "--high" in sys.argv:
    QUALITY = "high"
    RESOLUTION_X = 1920
    RESOLUTION_Y = 1080
    FPS = 60
    FRAME_END = 360  # 6 sec at 60 FPS

# ---- Scene Setup ----
print("Initializing subsystems...")
level_lib = unreal.EditorLevelLibrary
asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
editor_actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

# Clear level
print("Clearing level...")
all_actors = editor_actor_subsystem.get_all_level_actors()
for actor in all_actors:
    editor_actor_subsystem.destroy_actor(actor)
print("Level cleared")

# Ground
print("Spawning ground...")
ground_static_mesh = unreal.load_object(None, "/Engine/BasicShapes/Plane.Plane")
ground_actor = level_lib.spawn_actor_from_object(ground_static_mesh, unreal.Vector(0, 0, 0))
ground_actor.set_actor_scale3d(unreal.Vector(50, 50, 1))  # Larger ground
ground_actor.set_actor_label("Ground")
ground_mesh = ground_actor.get_component_by_class(unreal.StaticMeshComponent)
ground_mesh.set_simulate_physics(False)
ground_mesh.set_collision_enabled(unreal.ECollisionEnabled.QUERY_AND_PHYSICS)
ground_actor.set_mobility(unreal.ComponentMobility.STATIC)
print("Ground setup complete")

# Rock
print("Spawning rock...")
rock_static_mesh = unreal.load_object(None, "/Engine/BasicShapes/Sphere.Sphere")
rock_actor = level_lib.spawn_actor_from_object(rock_static_mesh, ROCK_LOCATION)
rock_actor.set_actor_label("Rock")
rock_actor.set_mobility(unreal.ComponentMobility.MOVABLE)

# Chaos Physics
print("Adding Chaos physics...")
rock_actor.add_component_by_class(unreal.GeometryCollectionComponent, False, unreal.Transform())
geo_comp = rock_actor.get_component_by_class(unreal.GeometryCollectionComponent)
geo_comp.set_simulate_physics(True)
geo_comp.set_mass_override_in_kg(ROCK_MASS)
geo_comp.set_damage_threshold([FRACTURE_THRESHOLD])
geo_comp.set_collision_enabled(unreal.ECollisionEnabled.QUERY_AND_PHYSICS)
geo_comp.set_initial_linear_velocity(unreal.Vector(0, 0, -200))  # Faster drop
geo_comp.enable_clustering(True)  # Better fracturing
print("Chaos physics applied")

# Material
print("Creating material...")
material = asset_tools.create_asset("RockMaterial", "/Game/Materials", unreal.Material, unreal.MaterialFactoryNew())
with unreal.ScopedEditorTransaction("Setting Material Properties"):
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
    material.set_editor_property("base_color", ROCK_COLOR)
    material.set_editor_property("roughness", 0.8)  # Rougher surface
    material.set_editor_property("metallic", 0.1)  # Slight metallic sheen
    material.set_editor_property("specular", 0.3)  # Subtle highlights
rock_mesh = rock_actor.get_component_by_class(unreal.StaticMeshComponent)
rock_mesh.set_material(0, material)
print("Material applied")

# Camera
print("Spawning camera...")
camera_actor = level_lib.spawn_actor_from_class(unreal.CineCameraActor, CAMERA_POS, CAMERA_ROT)
camera_actor.set_actor_label("RenderCamera")
camera = camera_actor.get_cine_camera_component()
camera.filmback.sensor_width = 36.0  # Full-frame sensor
camera.filmback.sensor_height = 20.25
camera.current_focal_length = 35.0  # Wide-angle lens
print("Camera setup complete")

# Light
print("Spawning light...")
light_actor = level_lib.spawn_actor_from_class(unreal.DirectionalLight, LIGHT_POS)
light_actor.set_actor_label("Sun")
light_comp = light_actor.get_component_by_class(unreal.DirectionalLightComponent)
light_comp.set_intensity(5.0)  # Softer light
light_comp.set_light_color(LIGHT_COLOR)
light_comp.set_dynamic_shadow_distance_movable_light(5000.0)  # Longer shadows
print("Light setup complete")

# Level Sequence
print("Creating level sequence...")
seq_path = "/Game/Sequences/RockShatterSeq"
sequence = asset_tools.create_asset("RockShatterSeq", "/Game/Sequences", unreal.LevelSequence, unreal.LevelSequenceFactoryNew())
sequence.set_playback_end(FRAME_END)
sequence.set_display_rate(unreal.FrameRate(FPS, 1))

# Add camera to sequence
camera_binding = sequence.add_possessable(camera_actor)
transform_track = camera_binding.add_track(unreal.MovieScene3DTransformTrack)
transform_section = transform_track.add_section()
transform_section.set_range(0, FRAME_END)
print("Sequence setup complete")

# Render Settings
print("Configuring render settings...")
queue_subsystem = unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem)
queue = queue_subsystem.get_queue()
job = queue.allocate_new_job(unreal.MoviePipelineExecutorJob)
job.set_sequence(unreal.SoftObjectPath(seq_path + ".RockShatterSeq"))
job.set_map(unreal.SoftObjectPath("/Engine/Maps/Entry.Entry"))

config = unreal.MoviePipelineMasterConfig()
job.set_configuration(config)

# Output settings
output_setting = config.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
output_setting.output_resolution = unreal.IntPoint(RESOLUTION_X, RESOLUTION_Y)
output_setting.output_directory = unreal.DirectoryPath("/home/matt/Projects/space/output")
output_setting.file_name_format = "rock_shatter.{frame_number}"
output_setting.zero_pad_frame_numbers = 4

# Anti-aliasing for smoother video
aa_setting = config.find_or_add_setting_by_class(unreal.MoviePipelineAntiAliasingSetting)
aa_setting.spatial_sample_count = 8  # Higher quality
aa_setting.temporal_sample_count = 4

# Execute render
print("Starting render...")
executor = unreal.MoviePipelineLocalExecutor()
queue_subsystem.render_queue_with_executor_instance(executor)
print(f"Rendering started! Check {output_setting.output_directory.path}/rock_shatter.mp4")