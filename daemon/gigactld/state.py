"""Persist the active profile so it survives a reboot (the EC resets to
firmware defaults on power-cycle; the daemon re-applies this at boot)."""
from __future__ import annotations

import json
import os

from . import curve

DEFAULT_PATH = "/var/lib/gigactl/state.json"


def snapshot(engine, kbd=None) -> dict:
    """Serialise the fan profile, plus the keyboard state when one is given.
    The keyboard rides in the same file so both restore from one atomic write;
    each domain owns its own dict shape (``kbd.to_dict()``) so this module never
    has to know the keyboard's fields."""
    data = {
        "profile": engine.profile,
        "linked": engine.linked,
        "cpu": [list(p) for p in engine.curve_for(curve.CPU_FAN)],
        "gpu": [list(p) for p in engine.curve_for(curve.GPU_FAN)],
    }
    if kbd is not None:
        data["keyboard"] = kbd.to_dict()
    return data


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
