"""Scenarios: pluggable ways to produce a motion cache.

A scenario is just a function with the signature

    fn(run_dir, logger, duration_seconds, frame_rate, physics_hz, config) -> {
        "npz_path": Path, "metadata": dict
    }

It owns HOW the motion is computed (Chrono, a NumPy integrator, …) and emits the
shared contract (motion.py) + run_metadata.json. The render side consumes the
contract and never needs to know which scenario produced it.

  rigid   - Project Chrono rigid bodies with contact + N-body gravity. True hard
            collisions (zero overlap), but the solver caps scale and the gravity
            loop is per-pair Python. Legacy per-body cache.
  gravity - Vectorized NumPy N-body of point particles, NO collisions (they pass
            through each other). Fastest; scales to many thousands.
  collide - Vectorized NumPy N-body + soft-sphere (DEM) collisions: particles
            attract AND bounce/pile when they overlap. The contact force is also
            the per-particle pressure signal a future heat sim would read.
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from pathlib import Path

import numpy as np

import contacts
import gravity
import motion
from logger import log_status
from physics import run_chrono_sim

_PALETTE = np.array([
    (0.90, 0.20, 0.20), (0.20, 0.55, 0.95), (0.25, 0.85, 0.35),
    (0.95, 0.80, 0.20), (0.65, 0.30, 0.90), (0.20, 0.85, 0.75),
    (0.95, 0.55, 0.20),
])


def _spawn_cloud(rng, n, cfg):
    """Uniform-in-sphere positions, random velocities, per-particle radius/color,
    unit masses. Shared by the gravity and collide scenarios."""
    radius = float(cfg["spawn_sphere_radius"])
    dirs = rng.normal(size=(n, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True) + 1e-12
    pos = dirs * (radius * rng.random(n)[:, None] ** (1.0 / 3.0))

    r_lo, r_hi = cfg.get("particle_radius_range", (0.2, 0.6))
    radii = rng.uniform(float(r_lo), float(r_hi), size=n)
    colors = _PALETTE[rng.integers(0, len(_PALETTE), size=n)]
    mass = np.ones(n)

    # If a dominant central seed will occupy the origin, keep cloud bodies OUT of
    # its volume. With collisions an initial overlap detonates the sim — but bodies
    # also must not be piled onto the seed's surface in a thin shell, or they
    # overlap each OTHER there and the contact solver squirts some back into the
    # seed, kicking it off-centre. So re-scatter each offender to a fresh random
    # spot in the clear shell (random direction, volume-uniform radius), redrawing
    # a few times until clear. Done BEFORE velocities so each launch stays radial.
    cm = float(cfg.get("central_mass", 0.0))
    c_radius = float(cfg.get("central_radius", 5.0))
    if cm > 0.0:
        clear = c_radius + radii + 0.5
        inside = np.where(np.linalg.norm(pos, axis=1) < clear)[0]
        for _ in range(4):
            if inside.size == 0:
                break
            ndir = rng.normal(size=(inside.size, 3))
            ndir /= np.linalg.norm(ndir, axis=1, keepdims=True) + 1e-12
            lo_r = np.minimum(clear[inside], radius)
            u = rng.random(inside.size)
            nr = (lo_r ** 3 + u * (radius ** 3 - lo_r ** 3)) ** (1.0 / 3.0)
            pos[inside] = ndir * nr[:, None]
            inside = np.where(np.linalg.norm(pos, axis=1) < clear)[0]

    lo, hi = sorted(abs(v) for v in cfg["spawn_lin_vel_range"])
    mag = rng.uniform(lo, hi, size=n)[:, None]
    if cfg.get("spawn_velocity_mode", "random") == "radial":
        # Coherent outward kick (an explosion): velocity points away from center,
        # so a bound cloud blows out then falls back instead of just puffing.
        rhat = pos / (np.linalg.norm(pos, axis=1, keepdims=True) + 1e-12)
        swirl = float(cfg.get("swirl_speed", 0.0))
        f = float(cfg.get("spin_fraction", 0.0))
        if swirl > 0.0 or f > 0.0:
            # Tangential direction (axis x r-hat): the coherent swirl, same sense
            # for every body, which injects net angular momentum.
            ax = np.asarray(cfg.get("spin_axis", (0.0, 1.0, 0.0)), dtype=float)
            ax /= np.linalg.norm(ax) + 1e-12
            tang = np.cross(ax[None, :], rhat)
            tang /= np.linalg.norm(tang, axis=1, keepdims=True) + 1e-12
        if swirl > 0.0:
            # Decoupled swirl (preferred): the FULL outward kick PLUS an independent
            # tangential speed on top. Explosion strength and spin no longer trade
            # against each other -> the balls fly away AND rotate.
            vel = rhat * mag + tang * swirl
        elif f > 0.0:
            # Legacy blend: spin_fraction steals from the outward kick to pay for
            # tangential, so high spin cancels the explosion. Kept for old configs.
            vdir = (1.0 - f) * rhat + f * tang
            vdir /= np.linalg.norm(vdir, axis=1, keepdims=True) + 1e-12
            vel = vdir * mag
        else:
            vel = rhat * mag
    else:
        vdir = rng.normal(size=(n, 3))
        vdir /= np.linalg.norm(vdir, axis=1, keepdims=True) + 1e-12
        vel = vdir * mag

    if cm > 0.0:
        # Inject the dominant central seed at index 0: heavy, large, at the origin
        # with zero velocity. Its volume was cleared above, so the cloud explodes
        # radially around it and it sits at the bottom of the potential well.
        c_color = np.asarray(cfg.get("central_color", (1.0, 0.85, 0.3)), dtype=float)
        pos = np.vstack(([0.0, 0.0, 0.0], pos))
        vel = np.vstack(([0.0, 0.0, 0.0], vel))
        radii = np.concatenate(([c_radius], radii))
        colors = np.vstack((c_color, colors))
        mass = np.concatenate(([cm], mass))
    return pos, vel, radii, colors, mass


def _body_defs(n, radii, colors):
    return [
        {"name": f"P_{i}", "shape": "sphere", "dims": {"radius": float(radii[i])},
         "color": [float(c) for c in colors[i]]}
        for i in range(n)
    ]


def _write(run_dir, scenario, seed, frames, frame_rate, duration_seconds, body_defs,
           cfg, frame_index, times, positions):
    npz_path = run_dir / "physics" / "motion_data.npz"
    motion.write_motion(npz_path, frame_index=frame_index, time=times, positions=positions)
    metadata = {
        "scenario": scenario,
        "seed": seed,
        "frames": frames,
        "frame_rate": frame_rate,
        "duration_seconds": duration_seconds,
        "bodies": body_defs,
        "body_count": len(body_defs),
        "gravity_const": cfg["gravity_const"],
        "obstacles": None,
    }
    (run_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2))
    return {"npz_path": npz_path, "metadata": metadata}


def _setup(run_dir, logger, cfg, label):
    (run_dir / "physics").mkdir(parents=True, exist_ok=True)
    seed = int(os.getenv("SIM_SEED", random.randint(0, 999_999)))
    rng = np.random.default_rng(seed)
    n = int(os.getenv("BODY_COUNT", cfg["default_body_count"]))
    logger.info("=== Physics (%s, vectorized) === (seed=%s, N=%d)", label, seed, n)
    if n >= 2500:
        logger.info("N=%d: gravity uses the Barnes-Hut tree; contacts (collide) use the O(N) grid.", n)
    return seed, rng, n


def run_gravity_sim(run_dir: Path, logger, duration_seconds: float, frame_rate: int,
                    physics_hz: int, config: dict) -> dict:
    """Point-particle N-body gravity, fully vectorized (leapfrog + Plummer
    softening). No collisions: particles pass through each other."""
    cfg = config
    seed, rng, n = _setup(run_dir, logger, cfg, "gravity")
    pos, vel, radii, colors, mass = _spawn_cloud(rng, n, cfg)
    n = pos.shape[0]   # may include an injected central seed body

    total_mass = float(mass.sum()) or 1.0
    g_eff = cfg["gravity_const"] / total_mass
    soft = float(cfg.get("gravity_softening", 1.0))
    solver = cfg.get("gravity_solver", "auto")
    theta = float(cfg.get("bh_theta", 0.5))
    # Collisionless gravity scales to tens of thousands with Barnes-Hut (O(N log N))
    # since there's no contact solver — the tree is the only thing left to make fast.
    logger.info("Gravity solver: %s", gravity.describe(solver, n, theta))

    def accel(p):
        return g_eff * gravity.gravity_accel(p, mass, soft, solver, theta)

    dt = 1.0 / max(1, physics_hz)
    steps_per_frame = max(1, int(math.ceil(physics_hz / frame_rate)))
    frames = int(math.ceil(duration_seconds * frame_rate))
    logger.info("Gravity: %d Hz, steps/frame=%d, frames=%d, g_eff=%.4g",
                physics_hz, steps_per_frame, frames, g_eff)

    positions = np.empty((frames, n, 3), dtype=np.float32)
    times = np.empty(frames, dtype=np.float32)
    frame_index = np.arange(frames, dtype=np.int32)

    a = accel(pos)
    t = 0.0
    for f in range(frames):
        positions[f] = pos
        times[f] = t
        for _ in range(steps_per_frame):
            vel += 0.5 * dt * a            # leapfrog (symplectic): KDK
            pos += dt * vel
            a = accel(pos)
            vel += 0.5 * dt * a
            t += dt
        log_status(logger, f"Gravity: frame {f + 1}/{frames}", overwrite=True)
    sys.stdout.write("\n")

    return _write(run_dir, "gravity", seed, frames, frame_rate, duration_seconds,
                  _body_defs(n, radii, colors), cfg, frame_index, times, positions)


def run_collide_sim(run_dir: Path, logger, duration_seconds: float, frame_rate: int,
                    physics_hz: int, config: dict) -> dict:
    """N-body gravity + soft-sphere (DEM) collisions, vectorized. Particles
    attract and bounce/pile when they overlap. Uses a fine internal timestep for
    contact stability (decoupled from render fps), and semi-implicit Euler (the
    DEM workhorse) since damping makes the system non-conservative by design."""
    cfg = config
    seed, rng, n = _setup(run_dir, logger, cfg, "collide")
    pos, vel, radii, colors, mass = _spawn_cloud(rng, n, cfg)
    n = pos.shape[0]   # may include an injected central seed body

    total_mass = float(mass.sum()) or 1.0
    g_eff = cfg["gravity_const"] / total_mass
    soft = float(cfg.get("gravity_softening", 1.0))
    solver = cfg.get("gravity_solver", "auto")
    theta = float(cfg.get("bh_theta", 0.5))
    k_contact = float(cfg.get("collision_stiffness", 20000.0))   # spring: higher = less overlap
    gamma = float(cfg.get("collision_damping", 30.0))            # dashpot: higher = less bouncy
    inv_mass = 1.0 / mass
    logger.info("Gravity solver: %s | contacts: O(N) spatial grid", gravity.describe(solver, n, theta))

    def accel(p, v):
        # Long-range gravity via the shared kernel (exact or Barnes-Hut).
        a = g_eff * gravity.gravity_accel(p, mass, soft, solver, theta)
        # Soft-sphere contacts via the O(N) spatial grid (contacts.py): only nearby
        # pairs are tested, so this scales to many thousands. The old dense all-pairs
        # version built an (N,N,3) array per step and capped the scenario at ~1-2k.
        a = a + contacts.contact_accel(p, v, radii, inv_mass, k_contact, gamma)
        return a

    # Contact stiffness needs a small dt; run physics at >= 240 Hz regardless of fps.
    eff_hz = max(physics_hz, int(cfg.get("collision_physics_hz", 240)))
    dt = 1.0 / eff_hz
    steps_per_frame = max(1, int(round(eff_hz / frame_rate)))
    frames = int(math.ceil(duration_seconds * frame_rate))
    logger.info("Collide: %d Hz (substeps/frame=%d), frames=%d, g_eff=%.4g, k=%.0f, damp=%.0f",
                eff_hz, steps_per_frame, frames, g_eff, k_contact, gamma)

    positions = np.empty((frames, n, 3), dtype=np.float32)
    times = np.empty(frames, dtype=np.float32)
    frame_index = np.arange(frames, dtype=np.int32)

    t = 0.0
    for f in range(frames):
        positions[f] = pos
        times[f] = t
        for _ in range(steps_per_frame):
            a = accel(pos, vel)
            vel += dt * a                  # semi-implicit (symplectic) Euler
            pos += dt * vel
            t += dt
        log_status(logger, f"Collide: frame {f + 1}/{frames}", overwrite=True)
    sys.stdout.write("\n")

    return _write(run_dir, "collide", seed, frames, frame_rate, duration_seconds,
                  _body_defs(n, radii, colors), cfg, frame_index, times, positions)


# name -> scenario function
SCENARIOS = {
    "rigid": run_chrono_sim,
    "gravity": run_gravity_sim,
    "collide": run_collide_sim,
}


def get_scenario(name: str):
    if name not in SCENARIOS:
        raise ValueError(f"Unknown scenario '{name}'. Available: {', '.join(SCENARIOS)}")
    return SCENARIOS[name]
