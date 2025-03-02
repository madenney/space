import hou
import sys
import random

# Quality mode: low (fast), high (slow), or superlow (fastest)
QUALITY = "low"  # Default
if "--high" in sys.argv:
    QUALITY = "high"
elif "--superlow" in sys.argv:
    QUALITY = "superlow"
FRAMES = 60 if QUALITY == "superlow" else 120  # 1 sec for superlow, 2 sec otherwise
FPS = 60
SAMPLES = 1 if QUALITY == "superlow" else (2 if QUALITY == "low" else 16)  # Ray samples
POINTS = 5 if QUALITY == "superlow" else (20 if QUALITY == "low" else 50)  # Fracture points

# Clear existing scene
for node in hou.node("/obj").children():
    node.destroy()
print("Scene cleared")

# Create ball geometry
geo = hou.node("/obj").createNode("geo", "ball")
sphere = geo.createNode("sphere")
sphere.parm("radx").set(0.5)
sphere.parm("tz").set(2)  # Start 2 units above ground
scatter = geo.createNode("scatter")
scatter.setInput(0, sphere)
scatter.parm("npts").set(POINTS)
fracture = geo.createNode("voronoifracture")
fracture.setInput(0, sphere)
fracture.setInput(1, scatter)
pack = geo.createNode("pack")
pack.setInput(0, fracture)
pack.setDisplayFlag(True)
print("Ball geometry created")

# Create ground geometry
ground = hou.node("/obj").createNode("geo", "ground")
plane = ground.createNode("grid")
plane.parm("sizex").set(10)
plane.parm("sizey").set(10)
ground_pack = ground.createNode("pack")
ground_pack.setInput(0, plane)
ground_pack.setDisplayFlag(True)
print("Ground geometry created")

# Add test cube for motion debug
test_cube = hou.node("/obj").createNode("geo", "test_cube")
cube = test_cube.createNode("box")
cube.parm("sizex").set(0.2)
cube.parm("sizey").set(0.2)
cube.parm("sizez").set(0.2)
cube.parm("tz").set(3)  # Above ball
print("Test cube created")

# Simulate with DOP Network
dopnet = hou.node("/obj").createNode("dopnet", "sim")
print("DOPNet created:", dopnet)
rbd = dopnet.createNode("rbdpackedobject", "ball_rbd")
print("RBD node created:", rbd)
if rbd is None:
    raise ValueError("Failed to create rbdpackedobject node")
rbd.parm("soppath").set("/obj/ball")
rbd.parm("pz").set(2)  # Explicit initial z-position
ground_rbd = dopnet.createNode("rbdpackedobject", "ground_rbd")
print("Ground RBD node created:", ground_rbd)
ground_rbd.parm("soppath").set("/obj/ground")
ground_rbd.parm("active").set(0)
test_rbd = dopnet.createNode("rbdpackedobject", "test_rbd")
print("Test RBD node created:", test_rbd)
test_rbd.parm("soppath").set("/obj/test_cube")
test_rbd.parm("pz").set(3)  # Explicit initial z-position

solver = dopnet.createNode("rigidbodysolver")
solver.setInput(0, rbd)
solver.setInput(1, ground_rbd)
solver.setInput(2, test_rbd)
gravity = dopnet.createNode("gravity")
gravity.parm("forcez").set(-9.81)
gravity.setInput(0, solver)
print("Solver and gravity created")
print("Cooking DOP network...")
dopnet.cook()
print("DOP network cooked")

# Import and cache simulation
sim_geo = hou.node("/obj").createNode("geo", "sim_output")
dopimport = sim_geo.createNode("dopimport")
dopimport.parm("doppath").set("/obj/sim")
dopimport.parm("importstyle").set("fetch")
cache = sim_geo.createNode("filecache", "sim_cache")
cache.setInput(0, dopimport)
cache.parm("file").set("/home/matt/Projects/space/output/sim_cache.$F4.bgeo.sc")
cache.parm("trange").set(1)
cache.parm("f1").set(1)
cache.parm("f2").set(FRAMES)
cache.setDisplayFlag(True)
print("Caching simulation...")
cache.cook()
print("Simulation cached")

# Set scene frame range and FPS
hou.playbar.setFrameRange(1, FRAMES)
hou.playbar.setPlaybackRange(1, FRAMES)
hou.setFps(FPS)
print(f"Frame range and FPS set: {hou.playbar.frameRange()}")

# Add camera (better side view)
cam = hou.node("/obj").createNode("cam", "cam1")
cam.parm("tx").set(6)
cam.parm("ty").set(2)
cam.parm("tz").set(0)
cam.parm("rx").set(-20)
cam.parm("ry").set(90)
print("Camera created: tx=", cam.parm("tx").eval(), "ty=", cam.parm("ty").eval(), "tz=", cam.parm("tz").eval(), "rx=", cam.parm("rx").eval())

# Render to EXR sequence
out = hou.node("/out").createNode("ifd", "render")
print("IFD node created:", out)
out.parm("camera").set("/obj/cam1")
out.parm("vm_picture").set("/home/matt/Projects/space/output/rock_sim.$F4.exr")
out.parm("trange").set(1)
out.parm("f1").set(1)
out.parm("f2").set(FRAMES)
print("Starting render...")
out.render()
print("Render complete")