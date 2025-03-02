from vpython import *
import random

# Scene setup
scene.title = "Ball Shattering Simulation with Fragments Landing"
scene.width = 800
scene.height = 600
scene.center = vector(0, 5, 0)

# Ground
ground = box(pos=vector(0, -1, 0), size=vector(20, 1, 20), color=color.green)

# Ball
ball = sphere(pos=vector(0, 10, 0), radius=1, color=color.red, make_trail=False)

# Initial conditions
ball.velocity = vector(0, -2, 0)  # Ball's initial velocity
g = vector(0, -9.8, 0)  # Gravity acceleration
dt = 0.01  # Time step
shattered = False  # To track if the ball shattered


def create_fragments(position, num_fragments=30):
    """Create small spheres (fragments) at the given position."""
    fragments = []
    for _ in range(num_fragments):
        # Randomize position and velocity of each fragment
        offset = vector(random.uniform(-0.5, 0.5), random.uniform(-0.5, 0.5), random.uniform(-0.5, 0.5))
        frag_velocity = vector(random.uniform(-5, 5), random.uniform(1, 5), random.uniform(-5, 5))
        fragment = sphere(pos=position + offset, radius=0.2, color=color.orange)
        fragment.velocity = frag_velocity
        fragments.append(fragment)
    return fragments


# Simulation loop
fragments = []  # List to hold fragments
while True:
    rate(100)  # Control simulation speed

    # If ball hasn't shattered yet
    if not shattered:
        ball.velocity += g * dt  # Update velocity
        ball.pos += ball.velocity * dt  # Update position

        # Check for collision with the ground
        if ball.pos.y <= ball.radius:
            shattered = True  # Ball hits the ground
            ball.visible = False  # Hide the ball
            fragments = create_fragments(ball.pos)  # Generate fragments

    # Update fragments' motion
    else:
        for frag in fragments:
            if frag.pos.y > frag.radius:  # If fragment is above the ground
                frag.velocity += g * dt  # Apply gravity to fragments
                frag.pos += frag.velocity * dt  # Update position
            else:  # Fragment hits the ground
                frag.velocity = vector(0, 0, 0)  # Stop its motion
                frag.pos.y = frag.radius  # Keep it exactly on the ground