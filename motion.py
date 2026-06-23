"""The motion contract: the single file format both the simulation and render
sides agree on. Physics writes it; Blender reads it; neither knows anything else
about the other.

Design rules (so it scales and stays forward-minded):
  - Structure-of-arrays, not array-of-structs: a few big flat NumPy arrays, never
    one tiny array per body. This is what lets both sides process it in bulk
    (vectorized / foreach_set) instead of per-element Python.
  - Pay only for what you write: orientations and extra channels are OPTIONAL. A
    gravity cloud writes positions only; a heat sim adds a "temperature" channel;
    nothing else in the pipeline changes.
  - float32 on disk (half the size of float64; plenty for rendering).

Layout of motion_data.npz:
  _format      : int            schema version (presence also marks "new format")
  frame_index  : (F,)   int32   the frame number of each sample
  time         : (F,)   float32  sim time (seconds) of each sample
  positions    : (F, N, 3) f32   body positions, in sim (Y-up) coordinates
  orientations : (F, N, 4) f32   OPTIONAL quaternions (wxyz); omit for point particles
  ch_<name>    : (F, N) or (F, N, k) f32   OPTIONAL per-body channels
                                           (e.g. ch_temperature -> shader color)

Body identity (name / shape / dims / color) stays static in run_metadata.json;
only things that vary frame-to-frame live here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

MOTION_FORMAT_VERSION = 1

# Keys that are part of the fixed schema; everything else prefixed ch_ is a channel.
_RESERVED = {"_format", "frame_index", "time", "positions", "orientations"}
_CHANNEL_PREFIX = "ch_"


def write_motion(
    path,
    *,
    frame_index,
    time,
    positions,
    orientations: Optional[Any] = None,
    channels: Optional[Dict[str, Any]] = None,
) -> Path:
    """Write a motion cache. positions is (F, N, 3); orientations (F, N, 4) wxyz
    if given; channels maps name -> (F, N) or (F, N, k)."""
    arrays: Dict[str, Any] = {
        "_format": np.asarray(MOTION_FORMAT_VERSION, dtype=np.int32),
        "frame_index": np.asarray(frame_index, dtype=np.int32),
        "time": np.asarray(time, dtype=np.float32),
        "positions": np.asarray(positions, dtype=np.float32),
    }
    if orientations is not None:
        arrays["orientations"] = np.asarray(orientations, dtype=np.float32)
    for name, arr in (channels or {}).items():
        arrays[f"{_CHANNEL_PREFIX}{name}"] = np.asarray(arr, dtype=np.float32)
    path = Path(path)
    np.savez(path, **arrays)
    return path


def is_new_format(path) -> bool:
    """True if the NPZ uses this contract (vs. the legacy per-body layout)."""
    with np.load(path) as data:
        return "_format" in data.files


def read_motion(path) -> Optional[Dict[str, Any]]:
    """Load a motion cache. Returns None if the file is the legacy per-body
    format (caller handles that path)."""
    data = np.load(path)
    if "_format" not in data.files:
        return None
    return {
        "format": int(data["_format"]),
        "frame_index": data["frame_index"],          # (F,)
        "time": data["time"],                         # (F,)
        "positions": data["positions"],               # (F, N, 3)
        "orientations": data["orientations"] if "orientations" in data.files else None,
        "channels": {
            k[len(_CHANNEL_PREFIX):]: data[k]
            for k in data.files
            if k.startswith(_CHANNEL_PREFIX)
        },
    }
