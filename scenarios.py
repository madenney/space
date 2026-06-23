"""Scenarios: pluggable ways to produce a motion cache.

A scenario is just a function with the signature

    fn(run_dir, logger, duration_seconds, frame_rate, physics_hz, config) -> {
        "npz_path": Path, "metadata": dict
    }

It owns HOW the motion is computed (Chrono, a NumPy integrator, SPH, …) and emits
the shared contract (motion.py) + run_metadata.json. The render side consumes the
contract and never needs to know which scenario produced it.

  rigid   - Project Chrono rigid bodies with contact + N-body gravity (the
            original sim; good for collisions / fracture). Legacy per-body cache.
  gravity - Vectorized NumPy N-body of point particles, no contact, no Chrono.
            All forces computed as array ops (no per-pair Python), so it scales to
            thousands. Writes the new structure-of-arrays contract.
"""
from __future__ import annotations

import json
import math
import os
import random
from pathlib import Path

import numpy as np

import motion
from logger import log_status
from physics import run_chrono_sim

_PALETTE = np.array([
    (0.90, 0.20, 0.20), (0.20, 0.55, 0.95), (0.25, 0.85, 0.35),
    (0.95, 0.80, 0.20), (0.65, 0.30, 0.90), (0.20, 0.85, 0.75),
    (0.95, 0.55, 0.20),
])


def run_gravity_sim(run_dir: Path, logger, duration_seconds: float, frame_rate: int,
                    physics_hz: int, config: dict) -> dict:
    """Point-particle N-body gravity, fully vectorized (leapfrog + Plummer
    softening). No Chrono, no per-pair Python loop."""
    cfg = config
    physics_dir = run_dir / "physics"
    physics_dir.mkdir(parents=True, exist_ok=True)

    seed = int(os.getenv("SIM_SEED", random.randint(0, 999_999)))
    rng = np.random.default_rng(seed)
    n = int(os.getenv("BODY_COUNT", cfg["default_body_count"]))
    logger.info("=== Physics (gravity, vectorized) === (seed=%s, N=%d)", seed, n)
    if n > 4000:
        logger.info("N=%d is large; the O(N^2) force array is ~%.0f MB/step. "
                    "Consider Barnes-Hut beyond this.", n, (n * n * 3 * 8) / 1e6)

    radius = float(cfg["spawn_sphere_radius"])
    # Uniform-in-sphere spawn (cube-root keeps it uniform by volume).
    dirs = rng.normal(size=(n, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True) + 1e-12
    pos = dirs * (radius * rng.random(n)[:, None] ** (1.0 / 3.0))

    # Per-particle render radius and color (static -> metadata).
    r_lo, r_hi = cfg.get("particle_radius_range", (0.2, 0.6))
    body_radii = rng.uniform(float(r_lo), float(r_hi), size=n)
    colors = _PALETTE[rng.integers(0, len(_PALETTE), size=n)]

    # Equal masses keep gravity body-count-independent via the g_eff normalization.
    mass = np.ones(n)
    total_mass = float(mass.sum()) or 1.0
    g_eff = cfg["gravity_const"] / total_mass
    soft2 = float(cfg.get("gravity_softening", 1.0)) ** 2

    # Initial velocities: random magnitude (spawn band), random direction.
    lo, hi = sorted(abs(v) for v in cfg["spawn_lin_vel_range"])
    vdir = rng.normal(size=(n, 3))
    vdir /= np.linalg.norm(vdir, axis=1, keepdims=True) + 1e-12
    vel = vdir * rng.uniform(lo, hi, size=n)[:, None]

    def accel(p: np.ndarray) -> np.ndarray:
        # a_i = g_eff * sum_j m_j (r_j - r_i) / (|r_j-r_i|^2 + eps^2)^(3/2)
        diff = p[None, :, :] - p[:, None, :]          # (N,N,3)
        r2 = (diff * diff).sum(-1) + soft2            # (N,N)
        inv_r3 = r2 ** -1.5
        np.fill_diagonal(inv_r3, 0.0)                 # no self-force
        w = (mass[None, :] * inv_r3)[:, :, None]      # (N,N,1)
        return g_eff * (w * diff).sum(axis=1)         # (N,3)

    dt = 1.0 / max(1, physics_hz)
    steps_per_frame = max(1, int(math.ceil(physics_hz / frame_rate)))
    frames = int(math.ceil(duration_seconds * frame_rate))
    logger.info("Gravity: %d Hz, dt=%.4g, steps/frame=%d, frames=%d, g_eff=%.4g",
                physics_hz, dt, steps_per_frame, frames, g_eff)

    positions = np.empty((frames, n, 3), dtype=np.float32)
    times = np.empty(frames, dtype=np.float32)
    frame_index = np.arange(frames, dtype=np.int32)

    a = accel(pos)
    t = 0.0
    for f in range(frames):
        positions[f] = pos
        times[f] = t
        for _ in range(steps_per_frame):
            vel += 0.5 * dt * a          # leapfrog: kick
            pos += dt * vel              #           drift
            a = accel(pos)
            vel += 0.5 * dt * a          #           kick
            t += dt
        log_status(logger, f"Gravity: frame {f + 1}/{frames}", overwrite=True)
    import sys
    sys.stdout.write("\n")

    npz_path = physics_dir / "motion_data.npz"
    motion.write_motion(npz_path, frame_index=frame_index, time=times, positions=positions)

    body_defs = [
        {"name": f"P_{i}", "shape": "sphere", "dims": {"radius": float(body_radii[i])},
         "color": [float(c) for c in colors[i]]}
        for i in range(n)
    ]
    metadata = {
        "scenario": "gravity",
        "seed": seed,
        "frames": frames,
        "frame_rate": frame_rate,
        "duration_seconds": duration_seconds,
        "bodies": body_defs,
        "body_count": n,
        "gravity_const": cfg["gravity_const"],
        "obstacles": None,
    }
    (run_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2))
    logger.info("Gravity stage complete: %d frames x %d particles -> %s", frames, n, npz_path)
    return {"npz_path": npz_path, "metadata": metadata}


# name -> scenario function
SCENARIOS = {
    "rigid": run_chrono_sim,
    "gravity": run_gravity_sim,
}


def get_scenario(name: str):
    if name not in SCENARIOS:
        raise ValueError(f"Unknown scenario '{name}'. Available: {', '.join(SCENARIOS)}")
    return SCENARIOS[name]
