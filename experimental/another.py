import pygame
import numpy as np
import time

# Initialize Pygame
pygame.init()
width, height = 800, 600
screen = pygame.display.set_mode((width, height))
pygame.display.set_caption("Smooth Mandelbrot Zoom")

# Colors
BLACK = (0, 0, 0)

# Mandelbrot parameters
max_iterations = 100
zoom_factor = 0.97  # Slower zoom for smoother feel
center_x, center_y = -0.75, 0.1
zoom_width, zoom_height = 3.0, 2.0
buffer_frames = 300  # Number of frames to precompute

# Precompute color gradient
colors = []
for i in range(max_iterations + 1):
    if i == max_iterations:
        colors.append(BLACK)
    else:
        r = int(255 * (i / max_iterations) ** 0.5)
        g = int(255 * (i / max_iterations))
        b = int(255 * (i / max_iterations) ** 2)
        colors.append((r, g, b))

# Clock for frame rate
clock = pygame.time.Clock()

def mandelbrot(x_min, x_max, y_min, y_max):
    x = np.linspace(x_min, x_max, width, dtype=np.float64)
    y = np.linspace(y_min, y_max, height, dtype=np.float64)
    X, Y = np.meshgrid(x, y)
    C = X + 1j * Y
    Z = np.zeros_like(C, dtype=np.complex128)
    img = np.full((height, width), max_iterations, dtype=int)

    for i in range(max_iterations):
        mask = np.abs(Z) <= 2
        Z[mask] = Z[mask] * Z[mask] + C[mask]
        img[np.logical_and(mask, np.abs(Z) > 2)] = i

    return img

# Precompute frames
print("Buffering frames... This might take a minute.")
start_time = time.time()
frame_buffer = []
current_width, current_height = zoom_width, zoom_height

for _ in range(buffer_frames):
    x_min = center_x - current_width / 2
    x_max = center_x + current_width / 2
    y_min = center_y - current_height / 2
    y_max = center_y + current_height / 2
    
    mandel = mandelbrot(x_min, x_max, y_min, y_max)
    surface = pygame.Surface((width, height))
    for y in range(height):
        for x in range(width):
            surface.set_at((x, y), colors[mandel[y, x]])
    
    frame_buffer.append(surface)
    current_width *= zoom_factor
    current_height *= zoom_factor

print(f"Buffering complete in {time.time() - start_time:.2f} seconds.")

# Main loop
running = True
frame_index = 0
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

    # Display current frame
    screen.blit(frame_buffer[frame_index], (0, 0))
    pygame.display.flip()

    # Move to next frame
    frame_index = (frame_index + 1) % buffer_frames

    # Aim for 60 FPS
    clock.tick(60)

pygame.quit()