import pybullet as p
import pybullet_data
import time
import random

# Connect to PyBullet GUI
p.connect(p.GUI)

# Set search path for PyBullet's example data
p.setAdditionalSearchPath(pybullet_data.getDataPath())

# Load a ground plane
plane = p.loadURDF("plane.urdf")

# Create a list to hold fragments (small spheres or cubes for shattering)
fragments = []

# Function to create robot fragments
def create_robot_fragments(position, num_fragments=30):
    """Create small fragments (spheres) at the specified position."""
    for _ in range(num_fragments):
        # Randomize fragment properties
        offset = [random.uniform(-0.5, 0.5), random.uniform(-0.5, 0.5), random.uniform(-0.5, 0.5)]
        frag_pos = [position[0] + offset[0], position[1] + offset[1], position[2] + offset[2]]
        frag_velocity = [random.uniform(-3, 3), random.uniform(-3, 3), random.uniform(1, 5)]

        # Create a small sphere or cube fragment
        fragment = p.loadURDF("sphere_small.urdf", basePosition=frag_pos)
        p.resetBaseVelocity(fragment, linearVelocity=frag_velocity)
        fragments.append(fragment)

# Load the robot (initially represented as a single box for simplicity)
robot_position = [0, 0, 5]  # Start position above the ground
robot = p.loadURDF("r2d2.urdf", basePosition=robot_position)

# Set gravity
p.setGravity(0, 0, -9.8)

# Simulation parameters
impact_detected = False  # Track when the robot hits the ground

# Main simulation loop
while True:
    p.stepSimulation()
    time.sleep(1 / 240)  # Run at ~240 FPS

    # Check for impact with the ground
    if not impact_detected:
        pos, _ = p.getBasePositionAndOrientation(robot)
        if pos[2] <= 0.3:  # When the robot is close to the ground
            impact_detected = True
            p.removeBody(robot)  # Remove the original robot
            create_robot_fragments(pos, num_fragments=50)  # Create fragments