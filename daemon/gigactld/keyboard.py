"""Single-zone RGB keyboard backlight control, via the shared EC mailbox.

Two gotchas, both proven on the G6 KF (see ``docs/PROTOCOL.md``):
1. a **master-enable** (doorbell 0xC4) must precede any colour/brightness
   command or the EC ignores it — so we send it before each set;
2. the EC takes colour components in **Blue, Red, Green** order, not RGB.

The mirror registers read back stale, so there is nothing to verify: we write
and trust the (hardware-proven) sequence.
"""
from __future__ import annotations

from dataclasses import dataclass

from .mailbox import command, pct_to_byte

DOORBELL_KB = 0xCA      # colour / brightness
DOORBELL_ENABLE = 0xC4  # master enable/disable

ENABLE_SUB = 0x0C
ENABLE_ON = 0x3F
ENABLE_OFF = 0x20

SUB_COLOR = 0x03
SUB_BRIGHT = 0x06


def _clamp8(v: int) -> int:
    return max(0, min(255, int(v)))


def set_enabled(ec, on: bool) -> None:
    """Master enable/disable of the backlight (also the gate colour/brightness
    commands require)."""
    command(ec, ENABLE_SUB, (ENABLE_ON if on else ENABLE_OFF,), doorbell=DOORBELL_ENABLE)


def set_color(ec, r: int, g: int, b: int) -> None:
    set_enabled(ec, True)
    command(ec, SUB_COLOR, (_clamp8(b), _clamp8(r), _clamp8(g)), doorbell=DOORBELL_KB)  # B,R,G


def set_brightness(ec, pct: int) -> None:
    if not 0 <= pct <= 100:
        raise ValueError(f"brightness percent out of range: {pct}")
    set_enabled(ec, True)
    command(ec, SUB_BRIGHT, (pct_to_byte(pct),), doorbell=DOORBELL_KB)


@dataclass
class KeyboardState:
    """The last backlight state the daemon was told to set. The EC forgets the
    backlight on power-cycle and on suspend, so the daemon holds this, persists
    it, and re-applies it at boot and on resume. The defaults are the firmware
    default (enabled, blue, full brightness) — used only as a starting point
    before the user sets anything; a fresh install with no saved state never
    applies these (see ``Daemon._restore_saved_state``)."""

    enabled: bool = True
    r: int = 0
    g: int = 0
    b: int = 255
    brightness_pct: int = 100

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "r": self.r, "g": self.g,
                "b": self.b, "brightness": self.brightness_pct}

    @classmethod
    def from_dict(cls, d: dict) -> "KeyboardState":
        """Rebuild from a (possibly hand-edited) state dict, tolerating missing
        keys and clamping brightness so a slightly-off value still applies."""
        return cls(
            enabled=bool(d.get("enabled", True)),
            r=int(d.get("r", 0)),
            g=int(d.get("g", 0)),
            b=int(d.get("b", 255)),
            brightness_pct=max(0, min(100, int(d.get("brightness", 100)))),
        )

    def apply(self, ec) -> None:
        """Re-drive the hardware to match this state (rgb is clamped downstream
        by ``set_color``)."""
        if self.enabled:
            set_color(ec, self.r, self.g, self.b)  # also master-enables
            set_brightness(ec, self.brightness_pct)
        else:
            set_enabled(ec, False)
