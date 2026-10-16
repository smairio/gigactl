"""How the keyboard backlight should read, moment to moment.

Kept GTK-free so it can be unit-tested without a display, the same split as
``temperature`` (pure) beside ``gauge`` (widget).

The effect maths mirror the daemon's ``lighting`` module *exactly* — the same
wall-clock phase, the same colour stops — so the preview, running in another
process, shows the colour the keys show at the same moment without asking the
daemon for it ten times a second.
"""
from __future__ import annotations

import colorsys
import math

from . import lighting

RGB = tuple[int, int, int]

# Mirrors gigactld.lighting.TEMP_STOPS.
TEMP_STOPS: tuple[tuple[int, RGB], ...] = (
    (45, (0, 0, 255)),
    (60, (0, 255, 0)),
    (72, (255, 180, 0)),
    (82, (255, 80, 0)),
    (92, (255, 0, 0)),
)


def intensity(enabled: bool, brightness_pct: int) -> float:
    """0.0–1.0 glow strength. Off means dark, and so does 0 % — no flattering
    floor, because the point of the preview is to match the hardware."""
    if not enabled:
        return 0.0
    return max(0, min(100, int(brightness_pct))) / 100


def hue_rgb(hue: float) -> RGB:
    r, g, b = colorsys.hsv_to_rgb(hue % 1.0, 1.0, 1.0)
    return (round(r * 255), round(g * 255), round(b * 255))


def temperature_rgb(temp_c: float | None) -> RGB:
    if temp_c is None or temp_c <= TEMP_STOPS[0][0]:
        return TEMP_STOPS[0][1]
    for (t0, c0), (t1, c1) in zip(TEMP_STOPS, TEMP_STOPS[1:]):
        if temp_c <= t1:
            f = (temp_c - t0) / (t1 - t0)
            return tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))
    return TEMP_STOPS[-1][1]


def frame(effect: str, period_s: int, rgb: RGB, glow: float, wall: float,
          cpu_temp: float | None = None) -> tuple[RGB, float]:
    """The (colour, glow) the keys show at wall-clock time ``wall``."""
    if glow <= 0:
        return rgb, 0.0
    if effect == lighting.CYCLE and period_s > 0:
        return hue_rgb((wall % period_s) / period_s), glow
    if effect == lighting.BREATHE and period_s > 0:
        phase = (wall % period_s) / period_s
        return rgb, max(0.01, glow * (0.5 - 0.5 * math.cos(math.tau * phase)))
    if effect == lighting.TEMPERATURE:
        return temperature_rgb(cpu_temp), glow
    return rgb, glow


def is_animated(effect: str) -> bool:
    return effect in (lighting.CYCLE, lighting.BREATHE)
