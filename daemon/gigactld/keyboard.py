"""Single-zone RGB keyboard backlight control, via the shared EC mailbox.

Two gotchas, both proven on the G6 KF (see ``docs/PROTOCOL.md``):
1. a **master-enable** (doorbell 0xC4) must precede any colour/brightness
   command or the EC ignores it — so we send it before each set;
2. the EC takes colour components in **Blue, Red, Green** order, not RGB.

The mirror registers read back stale, so there is nothing to verify: we write
and trust the (hardware-proven) sequence.

``set_color`` / ``set_brightness`` master-enable first, which is right for a
one-off set. Effects and the idle fade write many frames a second, so they use
the bare ``write_color`` / ``write_level`` instead, and the lighting driver
(``lighting.Driver``) master-enables once whenever the backlight comes on.
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


def write_color(ec, r: int, g: int, b: int) -> None:
    """Colour only, no master enable — for a backlight already known to be on."""
    command(ec, SUB_COLOR, (_clamp8(b), _clamp8(r), _clamp8(g)), doorbell=DOORBELL_KB)  # B,R,G


def write_level(ec, level: int) -> None:
    """Raw 0-255 brightness level, no master enable — for fade/effect frames."""
    command(ec, SUB_BRIGHT, (_clamp8(level),), doorbell=DOORBELL_KB)


def set_color(ec, r: int, g: int, b: int) -> None:
    set_enabled(ec, True)
    write_color(ec, r, g, b)


def set_brightness(ec, pct: int) -> None:
    if not 0 <= pct <= 100:
        raise ValueError(f"brightness percent out of range: {pct}")
    set_enabled(ec, True)
    write_level(ec, pct_to_byte(pct))


# --- lighting settings ---------------------------------------------------------
# What the backlight does over time. The names are the D-Bus values, the CLI
# words and the keys in state.json, so they must never be renamed.
STATIC = "static"            # one colour, steady
CYCLE = "cycle"              # slowly walk the colour wheel
BREATHE = "breathe"          # the chosen colour fades in and out
TEMPERATURE = "temperature"  # colour follows the CPU temperature
EFFECTS = (STATIC, CYCLE, BREATHE, TEMPERATURE)

# Seconds for one full loop. Only cycle and breathe have a speed.
DEFAULT_PERIOD_S = {CYCLE: 300, BREATHE: 6}
PERIOD_LIMITS_S = {CYCLE: (10, 24 * 3600), BREATHE: (2, 60)}

# Turn the light off after this long with no typing. 0 means never.
IDLE_LIMITS_S = (5, 24 * 3600)


def check_effect(effect: str, period_s: int) -> int:
    """Validate an effect request; return the period to store (0 = default).
    Raises ValueError with a sentence fit to show a user."""
    if effect not in EFFECTS:
        raise ValueError(f"unknown effect '{effect}' (choose: {', '.join(EFFECTS)})")
    if effect not in PERIOD_LIMITS_S or period_s == 0:
        return 0
    lo, hi = PERIOD_LIMITS_S[effect]
    if not lo <= period_s <= hi:
        raise ValueError(f"{effect} speed must be {lo}-{hi} seconds per loop, not {period_s}")
    return period_s


def check_idle_timeout(seconds: int) -> int:
    if seconds == 0:
        return 0
    lo, hi = IDLE_LIMITS_S
    if not lo <= seconds <= hi:
        raise ValueError(f"idle timeout must be 0 (never) or {lo}-{hi} seconds, not {seconds}")
    return seconds


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
    effect: str = STATIC
    period_s: int = 0           # 0 = the effect's default speed
    idle_timeout_s: int = 0     # 0 = never turn off for lack of typing
    notify_flash: bool = False  # the desktop app flashes the keys on notifications

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "r": self.r, "g": self.g,
                "b": self.b, "brightness": self.brightness_pct,
                "effect": self.effect, "period": self.period_s,
                "idle_timeout": self.idle_timeout_s,
                "notify_flash": self.notify_flash}

    @property
    def rgb(self) -> tuple[int, int, int]:
        return (self.r, self.g, self.b)

    def effective_period_s(self) -> int:
        """The loop length actually used: the stored one, or the default."""
        return self.period_s or DEFAULT_PERIOD_S.get(self.effect, 0)

    @classmethod
    def from_dict(cls, d: dict) -> KeyboardState:
        """Rebuild from a (possibly hand-edited) state dict, tolerating missing
        keys and clamping brightness so a slightly-off value still applies."""
        # Older daemons saved only the first five keys; a missing or bad
        # lighting setting falls back to "static, never idle" rather than
        # refusing the whole keyboard section.
        effect = d.get("effect", STATIC)
        try:
            period = check_effect(effect, int(d.get("period", 0)))
        except (ValueError, TypeError):
            effect, period = STATIC, 0
        try:
            idle = check_idle_timeout(int(d.get("idle_timeout", 0)))
        except (ValueError, TypeError):
            idle = 0
        return cls(
            enabled=bool(d.get("enabled", True)),
            r=int(d.get("r", 0)),
            g=int(d.get("g", 0)),
            b=int(d.get("b", 255)),
            brightness_pct=max(0, min(100, int(d.get("brightness", 100)))),
            effect=effect,
            period_s=period,
            idle_timeout_s=idle,
            notify_flash=bool(d.get("notify_flash", False)),
        )

    def record_colour(self, r: int, g: int, b: int) -> None:
        """Remember a colour the user set. Setting a colour also turns the
        backlight on, so ``enabled`` follows. Picking a colour while an effect
        that ignores the colour is running (cycle, temperature) means "show me
        this colour", so it returns to static; breathing uses the colour, so it
        keeps breathing in the new one."""
        self.r, self.g, self.b = r, g, b
        self.enabled = True
        if self.effect in (CYCLE, TEMPERATURE):
            self.effect, self.period_s = STATIC, 0

    def record_effect(self, effect: str, period_s: int) -> None:
        self.effect, self.period_s = effect, check_effect(effect, period_s)
        self.enabled = True

    def record_idle_timeout(self, seconds: int) -> None:
        self.idle_timeout_s = check_idle_timeout(seconds)

    def record_notify_flash(self, on: bool) -> None:
        self.notify_flash = bool(on)

    def record_brightness(self, pct: int) -> None:
        """Remember a brightness the user set (also turns the backlight on)."""
        self.brightness_pct = pct
        self.enabled = True

    def record_enabled(self, on: bool) -> None:
        """Remember the on/off state the user set."""
        self.enabled = on

    def apply(self, ec) -> None:
        """Re-drive the hardware to this state's *steady* look (rgb is clamped
        downstream by ``set_color``). The daemon itself goes through
        ``lighting.Driver``, which also handles effects and the idle fade; this
        stays for callers that just want the plain colour back."""
        if self.enabled:
            set_color(ec, self.r, self.g, self.b)  # also master-enables
            set_brightness(ec, self.brightness_pct)
        else:
            set_enabled(ec, False)
