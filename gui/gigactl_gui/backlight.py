"""How brightly the keyboard backlight should read.

Kept GTK-free so it can be unit-tested without a display, the same split as
``temperature`` (pure) beside ``gauge`` (widget).
"""
from __future__ import annotations


def intensity(enabled: bool, brightness_pct: int) -> float:
    """0.0–1.0 glow strength. Off means dark, and so does 0 % — no flattering
    floor, because the point of the preview is to match the hardware."""
    if not enabled:
        return 0.0
    return max(0, min(100, int(brightness_pct))) / 100
