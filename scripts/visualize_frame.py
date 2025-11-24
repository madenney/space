#!/usr/bin/env python3
"""
Quick inspector for Chrono NPZ data.

Usage:
    python visualize_frame.py -d output/output5 -f 0
    python visualize_frame.py -d output/output5/physics/motion_data.npz -f 12
"""
import argparse
import json
from pathlib import Path
from typing import Tuple

import matplotlib.pyplot as plt
import numpy as np


def parse_args():
    ap = argparse.ArgumentParser(description="Visualize a single Chrono frame from NPZ/metadata.")
    ap.add_argument(
        "-d",
        "--data",
        required=True,
        help="Path to run dir OR physics/motion_data.npz OR run_metadata.json.",
    )
    ap.add_argument("-f", "--frame", type=int, default=0, help="Frame index to visualize (0-based).")
    ap.add_argument("--show-ids", action="store_true", help="Draw body names next to shapes.")
    return ap.parse_args()


def resolve_paths(data_arg: str) -> Tuple[Path, Path]:
    p = Path(data_arg).expanduser().resolve()
    if p.is_file():
        if p.name.endswith(".npz"):
            npz_path = p
            # If file is .../physics/motion_data.npz, metadata lives two levels up
            run_dir = npz_path.parent.parent if npz_path.parent.name == "physics" else npz_path.parent
            meta_path = run_dir / "run_metadata.json"
        elif p.name == "run_metadata.json":
            meta_path = p
            run_dir = p.parent
            npz_path = run_dir / "physics" / "motion_data.npz"
        else:
            raise FileNotFoundError(f"Unrecognized data file: {p}")
    else:
        # Treat as run directory
        run_dir = p
        npz_path = run_dir / "physics" / "motion_data.npz"
        meta_path = run_dir / "run_metadata.json"
        # Fallback: allow data path directly to physics dir
        if not npz_path.exists():
            npz_path = p / "motion_data.npz"
    if not npz_path.exists():
        raise FileNotFoundError(f"NPZ not found at {npz_path}")
    if not meta_path.exists():
        raise FileNotFoundError(f"Metadata not found at {meta_path}")
    return npz_path, meta_path


def quat_to_mat(q):
    w, x, y, z = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def draw_box(ax, center, quat, sx, sy, sz, color):
    R = quat_to_mat(quat)
    ext = np.array([sx, sy, sz]) * 0.5
    corners = np.array(
        [[ex, ey, ez] for ex in (-ext[0], ext[0]) for ey in (-ext[1], ext[1]) for ez in (-ext[2], ext[2])]
    )
    corners = (R @ corners.T).T + center
    edges = [(0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3), (2, 6), (3, 7), (4, 5), (4, 6), (5, 7), (6, 7)]
    for i, j in edges:
        ax.plot(*zip(corners[i], corners[j]), color=color, linewidth=1)


def set_equal_axes(ax):
    xs, ys, zs = [], [], []
    for line in ax.get_lines():
        data = line.get_data_3d()
        xs.extend(data[0])
        ys.extend(data[1])
        zs.extend(data[2])
    if not xs:
        return
    ranges = [max(vals) - min(vals) for vals in (xs, ys, zs)]
    max_range = max(ranges) if max(ranges) > 0 else 1.0
    mid = [0.5 * (max(vals) + min(vals)) for vals in (xs, ys, zs)]
    half = max_range * 0.6
    ax.set_xlim(mid[0] - half, mid[0] + half)
    ax.set_ylim(mid[1] - half, mid[1] + half)
    ax.set_zlim(mid[2] - half, mid[2] + half)


def main():
    args = parse_args()
    npz_path, meta_path = resolve_paths(args.data)
    data = np.load(npz_path)
    meta = json.loads(meta_path.read_text())

    frame_idx = args.frame
    fig = plt.figure()
    ax = fig.add_subplot(projection="3d")
    ax.set_xlabel("X")
    ax.set_ylabel("Y (Chrono up)")
    ax.set_zlabel("Z")

    # Optional ground plane at y=0
    xs = np.linspace(-10, 10, 2)
    zs = np.linspace(-10, 10, 2)
    X, Z = np.meshgrid(xs, zs)
    Y = np.zeros_like(X)
    ax.plot_surface(X, Y, Z, color="gray", alpha=0.15, linewidth=0)

    for body_def in meta.get("bodies", []):
        arr = data[body_def["name"]]
        row = arr[arr[:, 0] == frame_idx]
        if len(row) == 0:
            continue
        row = row[0]
        pos = np.array([row[2], row[3], row[4]])
        quat = np.array([row[5], row[6], row[7], row[8]])
        color = body_def.get("color", (0.8, 0.2, 0.2))
        shape = body_def["shape"]
        if shape == "box":
            draw_box(ax, pos, quat, body_def["dims"]["sx"], body_def["dims"]["sy"], body_def["dims"]["sz"], color)
        elif shape == "sphere":
            ax.scatter(*pos, color=color, s=80)
        elif shape == "cylinder":
            ax.scatter(*pos, color=color, marker="^", s=80)
        if args.show_ids:
            ax.text(pos[0], pos[1], pos[2], body_def["name"], color="black", fontsize=8)

    ax.view_init(elev=30, azim=45)
    ax.set_title(f"Frame {frame_idx}")
    set_equal_axes(ax)
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
