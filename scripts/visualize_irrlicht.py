#!/usr/bin/env python3
"""
Irrlicht viewer for Chrono NPZ output.

Usage examples:
    python visualize_irrlicht.py -d output/output5 -f 0
    python visualize_irrlicht.py -d output/output5/physics/motion_data.npz --fps 30

Plays back the recorded poses from motion_data.npz/run_metadata.json using Chrono's Irrlicht renderer.
"""
import argparse
import json
import time
from pathlib import Path
from typing import Tuple

import numpy as np


def parse_args():
    ap = argparse.ArgumentParser(description="Irrlicht playback of Chrono NPZ frames.")
    ap.add_argument(
        "-d",
        "--data",
        required=True,
        help="Path to run dir OR physics/motion_data.npz OR run_metadata.json.",
    )
    ap.add_argument("-f", "--start-frame", type=int, default=0, help="Starting frame index (0-based).")
    ap.add_argument("--fps", type=float, default=None, help="Playback FPS (defaults to metadata fps).")
    ap.add_argument("--loop", action="store_true", help="Loop playback.")
    return ap.parse_args()


def resolve_paths(data_arg: str) -> Tuple[Path, Path]:
    p = Path(data_arg).expanduser().resolve()
    if p.is_file():
        if p.name.endswith(".npz"):
            npz_path = p
            run_dir = npz_path.parent.parent if npz_path.parent.name == "physics" else npz_path.parent
            meta_path = run_dir / "run_metadata.json"
        elif p.name == "run_metadata.json":
            meta_path = p
            run_dir = p.parent
            npz_path = run_dir / "physics" / "motion_data.npz"
        else:
            raise FileNotFoundError(f"Unrecognized data file: {p}")
    else:
        run_dir = p
        npz_path = run_dir / "physics" / "motion_data.npz"
        meta_path = run_dir / "run_metadata.json"
        if not npz_path.exists():
            npz_path = p / "motion_data.npz"
    if not npz_path.exists():
        raise FileNotFoundError(f"NPZ not found at {npz_path}")
    if not meta_path.exists():
        raise FileNotFoundError(f"Metadata not found at {meta_path}")
    return npz_path, meta_path


def build_scene(chrono, npz_data, meta):
    try:
        import pychrono.irrlicht as chronoirr
    except ImportError as exc:
        raise SystemExit("pychrono.irrlicht not available in this environment") from exc

    sys = chrono.ChSystemNSC()
    sys.Set_G_acc(chrono.ChVectorD(0, -9.81, 0))

    # Simple material and ground
    material = chrono.ChMaterialSurfaceNSC()
    material.SetFriction(0.6)
    material.SetRestitution(0.0)
    material.SetCompliance(0)

    ground = chrono.ChBodyEasyBox(20, 1, 20, 1000, True, False, material)
    ground.SetPos(chrono.ChVectorD(0, -0.5, 0))  # top face at y=0
    ground.SetBodyFixed(True)
    sys.Add(ground)

    # Obstacles (visual only)
    for part in meta.get("obstacles", {}).get("parts", []):
        if part.get("type") == "box":
            sx, sy, sz = part["size"]
            px, py, pz = part["pos"]
            rot = part.get("rot", [1, 0, 0, 0])
            obs = chrono.ChBodyEasyBox(sx, sy, sz, 500, True, False, material)
            obs.SetPos(chrono.ChVectorD(px, py, pz))
            obs.SetRot(chrono.ChQuaternionD(*rot))
            obs.SetBodyFixed(True)
            sys.Add(obs)

    # Animated bodies (visual only; we will set pose each frame)
    bodies = {}
    for body_def in meta.get("bodies", []):
        shape = body_def["shape"]
        dims = body_def["dims"]
        color = body_def.get("color", (0.8, 0.2, 0.2))
        if shape == "box":
            body = chrono.ChBodyEasyBox(dims["sx"], dims["sy"], dims["sz"], 500, True, False, material)
        elif shape == "sphere":
            body = chrono.ChBodyEasySphere(dims["radius"], 500, True, False, material)
        elif shape == "cylinder":
            body = chrono.ChBodyEasyCylinder(dims["radius"], dims["height"], 500, True, False, material)
        else:
            continue
        body.SetName(body_def["name"])
        body.SetBodyFixed(True)  # not simulated; driven by cached poses
        if body.GetVisualModel():
            try:
                n_shapes = body.GetVisualModel().GetNumShapes()
                for i in range(n_shapes):
                    shape_asset = body.GetVisualModel().GetShape(i)
                    if hasattr(shape_asset, "SetColor"):
                        shape_asset.SetColor(chrono.ChColor(*color))
            except Exception:
                pass
        sys.Add(body)
        bodies[body_def["name"]] = body

    app = chronoirr.ChIrrApp(sys, "Chrono NPZ Playback", chronoirr.dimension2du(1280, 720))
    app.AddTypicalSky()
    app.AddTypicalLights()
    app.AddTypicalCamera(chronoirr.vector3df(8, 6, 8), chronoirr.vector3df(0, 0.5, 0))
    app.AssetBindAll()
    app.AssetUpdateAll()

    return app, bodies


def main():
    args = parse_args()
    npz_path, meta_path = resolve_paths(args.data)
    try:
        import pychrono as chrono
        import pychrono.irrlicht  # noqa: F401
    except ImportError as exc:
        raise SystemExit("pychrono with irrlicht support is required to run this viewer.") from exc

    data = np.load(npz_path)
    meta = json.loads(meta_path.read_text())

    app, bodies = build_scene(chrono, data, meta)

    fps = args.fps or meta.get("frame_rate", 30)
    frame_dt = 1.0 / fps
    frame_idx = max(0, args.start_frame)
    max_frame_idx = max(0, meta.get("frames", 0) - 1)

    # Main playback loop
    while app.GetDevice().run():
        if frame_idx > max_frame_idx:
            if args.loop:
                frame_idx = 0
            else:
                break

        # Update driven poses
        for name, body in bodies.items():
            arr = data[name]
            row = arr[arr[:, 0] == frame_idx]
            if len(row) == 0:
                continue
            row = row[0]
            pos = chrono.ChVectorD(row[2], row[3], row[4])
            rot = chrono.ChQuaternionD(row[5], row[6], row[7], row[8])
            body.SetPos(pos)
            body.SetRot(rot)

        app.BeginScene()
        app.DrawAll()
        app.EndScene()

        frame_idx += 1
        time.sleep(frame_dt)


if __name__ == "__main__":
    main()
