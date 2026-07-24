"""The one semantic colour scale (DESIGN.md).

Cool < 75 °C → warm 75–89 °C → hot ≥ 90 °C. This scale is the app's signature
and is **only** ever used for temperatures, never decoratively. Dark-mode
variants lighten for contrast. The hex values are intentionally hardcoded here
(unlike libadwaita's own tokens) because this scale *is* the intent — it is not
a platform token.
"""
from __future__ import annotations

import enum

WARM_C = 75
HOT_C = 90

# Gauge display range: below 30 °C reads empty, 100 °C reads full.
GAUGE_MIN_C = 30
GAUGE_MAX_C = 100


class Band(enum.Enum):
    COOL = "cool"
    WARM = "warm"
    HOT = "hot"


# (light, dark) hex per band — dark lightens for contrast (DESIGN.md).
_HEX = {
    Band.COOL: ("#26a269", "#57c18a"),
    Band.WARM: ("#e5a50a", "#f0c44a"),
    Band.HOT: ("#e01b24", "#ff6b6b"),
}


def band(temp_c: float) -> Band:
    if temp_c >= HOT_C:
        return Band.HOT
    if temp_c >= WARM_C:
        return Band.WARM
    return Band.COOL


def color_hex(temp_c: float, *, dark: bool = False) -> str:
    light, dark_hex = _HEX[band(temp_c)]
    return dark_hex if dark else light


def color_rgb(temp_c: float, *, dark: bool = False) -> tuple[float, float, float]:
    """The same colour as 0–1 components, for the widgets that paint with Cairo
    (so nobody re-parses the hex)."""
    h = color_hex(temp_c, dark=dark).lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


def gauge_fraction(temp_c: float, lo: int = GAUGE_MIN_C, hi: int = GAUGE_MAX_C) -> float:
    """Fraction (0–1) of the gauge arc to fill for this temperature, clamped."""
    return max(0.0, min(1.0, (temp_c - lo) / (hi - lo)))


def summary(cpu_c: float, gpu_c: float) -> tuple[str, Band]:
    """Status-pill text + band for the hottest of the two sensors — answers
    'is my laptop OK?' at a glance (DESIGN.md hero)."""
    b = band(max(cpu_c, gpu_c))
    text = {
        Band.COOL: "Running cool",
        Band.WARM: "Warming up",
        Band.HOT: "Running hot",
    }[b]
    return text, b
