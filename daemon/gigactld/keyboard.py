"""Single-zone RGB keyboard backlight control.

Two gotchas, both proven on the G6 KF and documented in ``docs/PROTOCOL.md``:

1. a **master-enable** (doorbell 0xC4) must be sent before the EC honours any
   colour/brightness command — so we send it before each set;
2. the EC takes colour components in **Blue, Red, Green** order, not RGB.

The KLC*/KBBH mirror registers read back stale, so there is nothing to verify:
we write and trust the (hardware-proven) sequence.
"""
from __future__ import annotations

# mailbox registers (shared with fans.py, repeated here so this module reads standalone)
FCMD = 0xF8   # command / doorbell (written last)
FDAT = 0xF9   # sub-command
FBUF = 0xFA   # parameter 1
FBF1 = 0xFB   # parameter 2
FBF2 = 0xFC   # parameter 3

DOORBELL_KB = 0xCA      # colour / brightness commands
DOORBELL_ENABLE = 0xC4  # master enable/disable

ENABLE_SUB = 0x0C
ENABLE_ON = 0x3F
ENABLE_OFF = 0x20

SUB_COLOR = 0x03
SUB_BRIGHT = 0x06


def _clamp8(v: int) -> int:
    return max(0, min(255, int(v)))


def pct_to_level(pct: int) -> int:
    return round(max(0, min(100, pct)) * 255 / 100)


def _cmd(ec, fdat: int, fbuf: int = 0, fbf1: int = 0, fbf2: int = 0,
         doorbell: int = DOORBELL_KB) -> None:
    with ec.transaction():
        ec.write_u8(FDAT, fdat)
        ec.write_u8(FBUF, fbuf)
        ec.write_u8(FBF1, fbf1)
        ec.write_u8(FBF2, fbf2)
        ec.write_u8(FCMD, doorbell)


def set_enabled(ec, on: bool) -> None:
    """Master enable/disable of the backlight (also the gate colour/brightness
    commands require)."""
    _cmd(ec, ENABLE_SUB, ENABLE_ON if on else ENABLE_OFF, doorbell=DOORBELL_ENABLE)


def set_color(ec, r: int, g: int, b: int) -> None:
    set_enabled(ec, True)
    _cmd(ec, SUB_COLOR, _clamp8(b), _clamp8(r), _clamp8(g))  # EC order: B, R, G


def set_brightness(ec, pct: int) -> None:
    if not 0 <= pct <= 100:
        raise ValueError(f"brightness percent out of range: {pct}")
    set_enabled(ec, True)
    _cmd(ec, SUB_BRIGHT, pct_to_level(pct))
