"""Saved render presets, stored as JSON files in presets/.

A preset is a reusable job spec: any of quality / num_bodies / seconds /
first_frame, plus a config_override blob. Bare config files (e.g. a file that's
just {"gravity_const": 0.0081}) are normalized so their unknown keys fold into
config_override — that way hand-written overrides work as presets too.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PRESETS_DIR = PROJECT_ROOT / "presets"

_SPEC_KEYS = ("quality", "num_bodies", "seconds", "first_frame")


def _safe_name(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", (name or "").strip()).strip("_")
    return safe or "preset"


def _normalize(name: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    spec = {k: raw[k] for k in _SPEC_KEYS if raw.get(k) is not None}
    override: Dict[str, Any] = dict(raw.get("config_override") or {})
    # Any top-level key that isn't a known spec field is treated as a config override.
    for k, v in raw.items():
        if k not in _SPEC_KEYS and k not in ("config_override", "name"):
            override[k] = v
    return {"name": name, **spec, "config_override": override or None}


def list_presets() -> List[Dict[str, Any]]:
    if not PRESETS_DIR.exists():
        return []
    out: List[Dict[str, Any]] = []
    for f in sorted(PRESETS_DIR.glob("*.json")):
        try:
            raw = json.loads(f.read_text())
        except Exception:
            continue
        if isinstance(raw, dict):
            out.append(_normalize(f.stem, raw))
    return out


def save_preset(data: Dict[str, Any]) -> Dict[str, Any]:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    name = _safe_name(data.get("name", ""))
    body: Dict[str, Any] = {k: data[k] for k in _SPEC_KEYS if data.get(k) is not None}
    if data.get("config_override"):
        body["config_override"] = data["config_override"]
    (PRESETS_DIR / f"{name}.json").write_text(json.dumps(body, indent=2))
    return _normalize(name, body)


def delete_preset(name: str) -> bool:
    path = PRESETS_DIR / f"{_safe_name(name)}.json"
    if path.exists():
        path.unlink()
        return True
    return False
