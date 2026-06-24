import json
import math
import os
import random
import sys
from pathlib import Path

import numpy as np

from logger import log_status


def generate_random_euler(low: float = -1.0, high: float = 1.0):
    return (
        random.uniform(low, high),
        random.uniform(low, high),
        random.uniform(low, high),
    )


def run_chrono_sim(run_dir: Path, logger, duration_seconds: float, frame_rate: int, physics_hz: int, config: dict) -> dict:
    try:
        import pychrono as chrono
    except ModuleNotFoundError:
        logger.error("pychrono not found. Activate the Chrono environment first.")
        logger.error("Run: conda activate chrono")
        sys.exit(1)

    cfg = config
    physics_dir = run_dir / "physics"
    physics_dir.mkdir(parents=True, exist_ok=True)

    seed = int(os.getenv("SIM_SEED", random.randint(0, 999_999)))
    random.seed(seed)
    logger.info("=== Physics === (seed=%s)", seed)

    # Make collision shapes match render meshes closely (avoid padding that would desync Blender visuals)
    chrono.ChCollisionModel.SetDefaultSuggestedEnvelope(0.01)
    chrono.ChCollisionModel.SetDefaultSuggestedMargin(0.01)

    sys_chrono = chrono.ChSystemNSC()
    sys_chrono.Set_G_acc(chrono.ChVectorD(0, 0, 0))
    # Tighten solver for more accurate contacts; fall back if methods not available
    solver = sys_chrono.GetSolver()
    if hasattr(solver, "SetMaxIterations"):
        solver.SetMaxIterations(500)
    if hasattr(solver, "SetMaxItersStab"):
        solver.SetMaxItersStab(500)

    material = chrono.ChMaterialSurfaceNSC()
    material.SetFriction(0.6)
    material.SetRestitution(0.5)
    material.SetCompliance(0)

    def _tight_collision(body):
        # Force zero padding so contact shapes match render meshes
        try:
            body.GetCollisionModel().SetEnvelope(0.01)
            body.GetCollisionModel().SetSafeMargin(0.01)
        except Exception:
            pass

    container = []
    for ob_cfg in cfg.get("obstacle_configs", []):
        size = ob_cfg["size"]
        pos = ob_cfg["pos"]
        rx, ry, rz = ob_cfg.get("euler", (0.0, 0.0, 0.0))
        rot_q = chrono.Q_from_Euler123(chrono.ChVectorD(rx, ry, rz))
        wall = chrono.ChBodyEasyBox(*size, 500, True, True, material)
        wall.SetPos(chrono.ChVectorD(*pos))
        wall.SetRot(rot_q)
        wall.SetBodyFixed(True)
        wall.SetCollide(True)
        _tight_collision(wall)
        sys_chrono.Add(wall)
        container.append(
            {
                "type": "box",
                "size": size,
                "pos": pos,
                "rot": [rot_q.e0, rot_q.e1, rot_q.e2, rot_q.e3],
            }
        )

    body_count = int(os.getenv("BODY_COUNT", cfg["default_body_count"]))
    bodies = []
    body_defs = []
    # Track spawn boxes to avoid initial overlaps
    spawn_extents = []

    def spawn_body(idx: int):
        shapes = list(cfg["shape_weights"].keys())
        weights = list(cfg["shape_weights"].values())
        shape = random.choices(shapes, weights=weights, k=1)[0]
        name = f"Body_{idx}"
        if shape == "box":
            box_cfg = cfg["shape_dim_config"]["box"]
            xmin, ymin, zmin = box_cfg["min"]
            xmax, ymax, zmax = box_cfg["max"]
            size = (
                random.uniform(xmin, xmax),
                random.uniform(ymin, ymax),
                random.uniform(zmin, zmax),
            )
            axis = random.choice([0, 1, 2])
            size = list(size)
            size[axis] = max(size[axis], size[axis])
            size = tuple(size)
            body = chrono.ChBodyEasyBox(*size, 500, True, True, material)
            dims = {"sx": size[0], "sy": size[1], "sz": size[2]}
        elif shape == "sphere":
            sphere_cfg = cfg["shape_dim_config"]["sphere"]
            radius = random.uniform(sphere_cfg["radius_min"], sphere_cfg["radius_max"])
            body = chrono.ChBodyEasySphere(radius, 500, True, True, material)
            dims = {"radius": radius}
            # Mass scales with volume at the configured density (water-like)
            volume = (4.0 / 3.0) * math.pi * (radius ** 3)
            mass = cfg["body_density"] * volume
            inertia = (2.0 / 5.0) * mass * (radius ** 2)
            body.SetMass(mass)
            body.SetInertiaXX(chrono.ChVectorD(inertia, inertia, inertia))
        else:
            cyl_cfg = cfg["shape_dim_config"]["cylinder"]
            radius = random.uniform(cyl_cfg["radius_min"], cyl_cfg["radius_max"])
            height = random.uniform(cyl_cfg["height_min"], cyl_cfg["height_max"])
            body = chrono.ChBodyEasyCylinder(radius, height, 500, True, True, material)
            dims = {"radius": radius, "height": height}

        body.SetName(name)
        axis = chrono.ChVectorD(
            random.uniform(-1, 1),
            random.uniform(-1, 1),
            random.uniform(-1, 1),
        )
        if axis.Length() < 1e-4:
            axis = chrono.ChVectorD(1, 0, 0)
        axis.Normalize()
        angle = random.uniform(0, math.pi * 2)
        body.SetRot(chrono.Q_from_AngAxis(angle, axis))

        def extents_for_shape():
            if shape == "box":
                return (size[0] / 2.0, size[1] / 2.0, size[2] / 2.0)
            if shape == "sphere":
                return (dims["radius"], dims["radius"], dims["radius"])
            # cylinder extents
            return (dims["radius"], dims["height"] / 2.0, dims["radius"])

        ext = extents_for_shape()

        def overlaps(p, e):
            for (op, oe) in spawn_extents:
                if (
                    abs(p[0] - op[0]) <= (e[0] + oe[0])
                    and abs(p[1] - op[1]) <= (e[1] + oe[1])
                    and abs(p[2] - op[2]) <= (e[2] + oe[2])
                ):
                    return True
            return False

        def random_dir():
            for _ in range(20):
                vx, vy, vz = (random.uniform(-1, 1) for _ in range(3))
                norm = math.sqrt(vx * vx + vy * vy + vz * vz)
                if norm > 1e-6:
                    return (vx / norm, vy / norm, vz / norm)
            return (1.0, 0.0, 0.0)

        attempt = 0
        max_attempts = 80
        pos_tuple = (0.0, 0.0, 0.0)
        max_extent = max(ext)
        spawn_radius = max(cfg["spawn_sphere_radius"] - max_extent, 0.1)
        while attempt < max_attempts:
            r = spawn_radius * (random.random() ** (1.0 / 3.0))
            dir_vec = random_dir()
            pos_tuple = (dir_vec[0] * r, dir_vec[1] * r, dir_vec[2] * r)
            if not overlaps(pos_tuple, ext):
                break
            attempt += 1
        pos = chrono.ChVectorD(*pos_tuple)
        body.SetPos(pos)
        spawn_extents.append((pos_tuple, ext))

        lin_low, lin_high = sorted(abs(v) for v in cfg["spawn_lin_vel_range"])
        ang_low, ang_high = sorted(abs(v) for v in cfg["spawn_ang_vel_range"])
        if lin_high > 0:
            if cfg.get("spawn_velocity_mode", "random") == "radial":
                # Coherent outward kick (an explosion): velocity points away from
                # the center, so a bound cloud blows out then falls back.
                px, py, pz = pos_tuple
                pn = math.sqrt(px * px + py * py + pz * pz) or 1.0
                ldir = (px / pn, py / pn, pz / pn)
            else:
                ldir = random_dir()
            lmag = random.uniform(lin_low, lin_high)
            body.SetPos_dt(chrono.ChVectorD(ldir[0] * lmag, ldir[1] * lmag, ldir[2] * lmag))
        else:
            body.SetPos_dt(chrono.ChVectorD(0, 0, 0))
        if ang_high > 0:
            adir = random_dir()
            amag = random.uniform(ang_low, ang_high)
            body.SetWvel_par(chrono.ChVectorD(adir[0] * amag, adir[1] * amag, adir[2] * amag))
        else:
            body.SetWvel_par(chrono.ChVectorD(0, 0, 0))
        body.SetUseSleeping(False)
        body.SetCollide(True)
        _tight_collision(body)
        sys_chrono.Add(body)

        palette = [
            (0.90, 0.20, 0.20),
            (0.20, 0.55, 0.95),
            (0.25, 0.85, 0.35),
            (0.95, 0.80, 0.20),
            (0.65, 0.30, 0.90),
            (0.20, 0.85, 0.75),
            (0.95, 0.55, 0.20),
        ]
        color = random.choice(palette)
        body_defs.append({"name": name, "shape": shape, "dims": dims, "color": color})
        return body

    for idx in range(body_count):
        bodies.append(spawn_body(idx))
    logger.info("Spawned %d objects", len(bodies))

    motion_data = {b.GetName(): [] for b in bodies}
    target_frames = int(math.ceil(duration_seconds * frame_rate))
    sample_dt = 1.0 / frame_rate
    phys_dt = 1.0 / max(1, physics_hz)
    steps_per_frame = max(1, int(math.ceil(physics_hz / frame_rate)))
    total_phys_steps = target_frames * steps_per_frame
    sim_time = 0.0
    next_sample = 0.0
    frame = 0
    logger.info(
        "Physics: %d Hz, duration=%.2fs, phys_steps=%d, output_frames=%d (frame_rate=%d)",
        physics_hz,
        duration_seconds,
        total_phys_steps,
        target_frames,
        frame_rate,
    )
    steps_done = 0
    # Normalize gravity by total mass so the felt pull is independent of body
    # count: the per-body acceleration works out to ~gravity_const / r², instead
    # of ~gravity_const * M_total / r². So adding bodies no longer cranks gravity.
    total_mass = sum(b.GetMass() for b in bodies) or 1.0
    g_eff = cfg["gravity_const"] / total_mass
    logger.info("Gravity normalized: G_const=%.4g / total_mass=%.4g -> g_eff=%.4g",
                cfg["gravity_const"], total_mass, g_eff)
    # Static masses for the vectorized gravity (masses don't change during the sim).
    masses_np = np.array([b.GetMass() for b in bodies], dtype=float)
    gravity_on = len(bodies) > 1 and cfg["gravity_const"] > 0
    # Plummer softening length²: bounds the 1/r² attraction as bodies get close,
    # so a near-collision can't generate a near-infinite impulse and blow up.
    soft_sq = float(cfg.get("gravity_softening", 1.0)) ** 2
    log_status(logger, f"Physics steps: 0/{total_phys_steps} | frames: 0/{target_frames}", overwrite=True)
    while frame < target_frames:
        if gravity_on:
            for b in bodies:
                if hasattr(b, "Empty_forces_accumulators"):
                    b.Empty_forces_accumulators()
                elif hasattr(b, "EmptyForcesAccumulators"):
                    b.EmptyForcesAccumulators()
                else:
                    # Fallback: zero forces/torques directly
                    b.SetForce(chrono.ChVectorD(0, 0, 0))
                    b.SetTorque(chrono.ChVectorD(0, 0, 0))
            # Same softened N-body force as the old O(N²) Python loop —
            # F_i = Σ_j g_eff·mᵢmⱼ (r_j-r_i)/(r²+ε²)^1.5 — but computed in NumPy
            # (one C-level pass) instead of a per-pair interpreter loop. The
            # contact solver below (DoStepDynamics) is untouched.
            pos_np = np.array([(p.x, p.y, p.z) for p in (b.GetPos() for b in bodies)])
            diff = pos_np[None, :, :] - pos_np[:, None, :]        # (N,N,3) r_j - r_i
            inv = (np.square(diff).sum(-1) + soft_sq) ** -1.5     # (N,N) 1/(r²+ε²)^1.5
            np.fill_diagonal(inv, 0.0)                            # no self-force
            w = (g_eff * masses_np[:, None] * masses_np[None, :] * inv)[:, :, None]
            forces = (w * diff).sum(axis=1)                       # (N,3) net force per body
            for i, body_i in enumerate(bodies):
                fi = forces[i]
                body_i.Accumulate_force(
                    chrono.ChVectorD(float(fi[0]), float(fi[1]), float(fi[2])),
                    body_i.GetPos(), False)

        sys_chrono.DoStepDynamics(phys_dt)
        sim_time += phys_dt
        steps_done += 1
        log_status(logger, f"Physics steps: {steps_done}/{total_phys_steps} | frames: {frame}/{target_frames}", overwrite=True)
        if sim_time + 1e-9 >= next_sample:
            for body in bodies:
                pos = body.GetPos()
                rot = body.GetRot()
                motion_data[body.GetName()].append(
                    [
                        frame,
                        sys_chrono.GetChTime(),
                        pos.x,
                        pos.y,
                        pos.z,
                        rot.e0,
                        rot.e1,
                        rot.e2,
                        rot.e3,
                    ]
                )
            frame += 1
            next_sample += sample_dt
            log_status(logger, f"Physics steps: {steps_done}/{total_phys_steps} | frames: {frame}/{target_frames}", overwrite=True)
        if steps_done >= total_phys_steps and frame < target_frames:
            # Catch-up: if we hit the planned step budget but still owe frames, extend steps.
            total_phys_steps += steps_per_frame
    sys.stdout.write("\n")

    npz_path = physics_dir / "motion_data.npz"
    np.savez(npz_path, **{k: np.array(v) for k, v in motion_data.items()})
    frames_recorded = frame
    if frames_recorded != target_frames:
        logger.info("Chrono frames recorded: %d (target %d)", frames_recorded, target_frames)
    else:
        logger.info("Chrono stage complete: %d frames for %d bodies -> %s", frames_recorded, len(bodies), npz_path)

    metadata = {
        "seed": seed,
        "frames": frames_recorded,
        "frame_rate": frame_rate,
        "duration_seconds": duration_seconds,
        "bodies": body_defs,
        "body_count": body_count,
        "gravity_const": cfg["gravity_const"],
        "obstacles": {"parts": container, "color": cfg.get("obstacle_color", (0.20, 0.22, 0.26))},
    }
    (run_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2))
    return {"npz_path": npz_path, "metadata": metadata}
