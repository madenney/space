import os

try:
    import pychrono as chrono
    import pychrono.irrlicht as chronoirr
except ModuleNotFoundError:
    print("Make sure to activate Conda environment 'chrono'!")
    print("Run this command: conda activate chrono")
    exit(1)

# Initialize Chrono system
sys = chrono.ChSystemNSC()
sys.Set_G_acc(chrono.ChVectorD(0, -9.81, 0))  # Gravity in m/s^2

# Output directory
output_dir = "output"
if not os.path.exists(output_dir):
    os.makedirs(output_dir)

# Material
material = chrono.ChMaterialSurfaceNSC()
material.SetFriction(0.5)
material.SetRestitution(0.2)

# Ground
ground = chrono.ChBodyEasyBox(20, 1, 20, 1000, True, True, material)
ground.SetPos(chrono.ChVectorD(0, -0.5, 0))
ground.SetBodyFixed(True)
sys.Add(ground)

# Falling rock (box)
rock = chrono.ChBodyEasyBox(0.5, 0.5, 0.5, 1000, True, True, material)
rock.SetPos(chrono.ChVectorD(0, 4, 0))
sys.Add(rock)

# Irrlicht visualization
vis = chronoirr.ChVisualSystemIrrlicht()
vis.AttachSystem(sys)
vis.SetWindowSize(800, 600)
vis.SetWindowTitle("Falling Rock Simulation")
vis.Initialize()
vis.AddSkyBox()
vis.AddCamera(chrono.ChVectorD(5, 5, 5), chrono.ChVectorD(0, 1, 0))
vis.AddTypicalLights()


# Simulation loop (2 seconds)
time_step = 1e-2  # 10ms steps, ~200 frames for 2s
frame_count = 0
while vis.Run() and sys.GetChTime() < 2.0:
    sys.DoStepDynamics(time_step)
    vis.BeginScene()
    vis.Render()
    vis.EndScene()
    frame_file = os.path.join(output_dir, f"frame_{frame_count:04d}.bmp")
    vis.WriteImageToFile(frame_file)
    frame_count += 1

vis.GetDevice().closeDevice()
print(f"Simulation complete. Frames saved to {output_dir}")