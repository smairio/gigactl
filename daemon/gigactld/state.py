"""Persist the active profile so it survives a reboot (the EC resets to
firmware defaults on power-cycle; the daemon re-applies this at boot)."""
from __future__ import annotations

import json
import os

from . import curve

DEFAULT_PATH = "/var/lib/gigactl/state.json"


def snapshot(engine) -> dict:
    return {
        "profile": engine.profile,
        "linked": engine.linked,
        "cpu": [list(p) for p in engine.curve_for(curve.CPU_FAN)],
        "gpu": [list(p) for p in engine.curve_for(curve.GPU_FAN)],
    }


def save(path: str, data: dict) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)  # atomic


def load(path: str):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def restore(engine, data: dict) -> None:
    """Apply a loaded snapshot to an engine. Manual/unknown profiles fall back
    to firmware — 'manual' is a transient override that shouldn't have been
    saved, and firmware is the safe default."""
    profile = data.get("profile", curve.FIRMWARE)
    if profile == curve.CUSTOM:
        cpu = [tuple(p) for p in data.get("cpu", [])]
        gpu = [tuple(p) for p in data.get("gpu", [])]
        engine.set_custom(cpu, gpu or None, linked=data.get("linked", True))
    elif profile in curve.PROFILES or profile == curve.FIRMWARE:
        engine.set_profile(profile)
    else:
        engine.set_profile(curve.FIRMWARE)
