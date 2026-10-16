"""What the keyboard backlight should show *right now*, and getting it there.

The saved ``KeyboardState`` says what the user chose; this module turns that
into a moment-by-moment ``Look`` — with the idle fade-out, the running effect
and any notification flash folded in — and drives the EC to it.

It is split the same way as the fan code (``curve`` decides, ``service`` acts):

* :class:`Lighting` is pure: give it a time, it says what to show and when the
  answer next changes. No GLib, no EC, so every timing rule is unit-tested with
  a fake clock.
* :class:`Driver` owns the hardware side: it remembers what the EC is showing
  and writes only what differs, so a steady colour costs nothing per frame and
  a fade costs one brightness command per step.

Two clocks are used on purpose. *Monotonic* time measures idleness, because a
wall-clock jump (NTP, a manual change) must never darken or wake the keys.
*Wall* time sets the phase of the colour cycle and breathing, so the desktop
app's preview — another process — can compute the same colour at the same
moment without asking the daemon 10 times a second.
"""
from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass

from . import keyboard
from .keyboard import BREATHE, CYCLE, STATIC, TEMPERATURE, KeyboardState  # noqa: F401
from .mailbox import pct_to_byte

RGB = tuple[int, int, int]

# Going dark: a gentle fade rather than a snap, like the Windows Control
# Center. Coming back is instant — the first key press should light the keys.
FADE_OUT_S = 2.0
FADE_STEP_S = 0.08

# Breathing redraws at ~12 fps: smooth to the eye, one EC command per frame.
BREATHE_STEP_S = 0.08
# Never breathe all the way to zero: level 0 would be a master-disable and
# re-enable on every breath, which is extra EC traffic for no visible gain.
BREATHE_MIN_LEVEL = 2

# The colour cycle changes hue ~2 degrees per step; slow loops step at most
# every few seconds, fast ones no faster than 10 times a second.
CYCLE_STEPS_PER_LOOP = 180
CYCLE_STEP_LIMITS_S = (0.1, 5.0)

# Notification flash: a few bright white blinks, then back to normal. White
# reads on every colour, and on a dark (idle) keyboard too.
FLASH_RGB: RGB = (255, 255, 255)
FLASH_ON_S = 0.18
FLASH_OFF_S = 0.18
FLASH_PULSES = 3
FLASH_MIN_LEVEL = 102  # ~40 %: visible even when the user keeps the keys dim

# CPU temperature -> colour. Cool is the firmware's own blue, hot is red,
# blended in between. Smoothed so a 2-second spike does not strobe the keys.
TEMP_STOPS: tuple[tuple[int, RGB], ...] = (
    (45, (0, 0, 255)),     # cool
    (60, (0, 255, 0)),     # working
    (72, (255, 180, 0)),   # warm
    (82, (255, 80, 0)),    # hot
    (92, (255, 0, 0)),     # very hot
)
TEMP_SMOOTHING = 0.35  # weight of the newest reading


@dataclass(frozen=True)
class Look:
    """What the backlight shows: on or off, and if on, which colour at which
    raw 0-255 level."""
    on: bool
    rgb: RGB = (0, 0, 0)
    level: int = 0


DARK = Look(False)


# --- pure colour maths -----------------------------------------------------------

def hue_rgb(hue: float) -> RGB:
    """A fully saturated colour for ``hue`` in [0, 1) — the colour wheel."""
    r, g, b = colorsys.hsv_to_rgb(hue % 1.0, 1.0, 1.0)
    return (round(r * 255), round(g * 255), round(b * 255))


def cycle_rgb(wall: float, period_s: int) -> RGB:
    return hue_rgb((wall % period_s) / period_s)


def breathe_factor(wall: float, period_s: int) -> float:
    """0..1 brightness multiplier: dark at the start of each loop, full halfway."""
    phase = (wall % period_s) / period_s
    return 0.5 - 0.5 * math.cos(math.tau * phase)


def temperature_rgb(temp_c: float | None) -> RGB:
    if temp_c is None:
        return TEMP_STOPS[0][1]
    if temp_c <= TEMP_STOPS[0][0]:
        return TEMP_STOPS[0][1]
    for (t0, c0), (t1, c1) in zip(TEMP_STOPS, TEMP_STOPS[1:]):
        if temp_c <= t1:
            f = (temp_c - t0) / (t1 - t0)
            return tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))
    return TEMP_STOPS[-1][1]


# --- the state machine -----------------------------------------------------------

class Lighting:
    """Decides the look from the user's settings, the time since the last key
    press, the CPU temperature and any flash in progress.

    ``settings`` is the daemon's live ``KeyboardState`` (shared, not copied), so
    a setting changed over D-Bus is seen on the very next frame.
    """

    def __init__(self, settings: KeyboardState | None = None) -> None:
        self.settings = settings
        self.last_activity = 0.0
        self.temp: float | None = None
        self._flash_start: float | None = None

    # --- inputs --------------------------------------------------------------
    def note_activity(self, now: float) -> bool:
        """A key was pressed (or the user changed a setting). Returns True when
        that changes what should be shown — the keys were dimmed or dimming —
        so the caller knows to redraw at once rather than at the next tick."""
        was_dimmed = self.idle_factor(now) < 1.0
        self.last_activity = now
        return was_dimmed

    def note_temperature(self, temp_c: int) -> None:
        if self.temp is None:
            self.temp = float(temp_c)
        else:
            self.temp += TEMP_SMOOTHING * (temp_c - self.temp)

    def start_flash(self, now: float) -> bool:
        """Begin a notification flash. Ignored (returns False) while the light
        is switched off, or while a flash is already running — a burst of
        notifications should not turn into a strobe."""
        s = self.settings
        if s is None or not s.enabled or self.flashing(now):
            return False
        self._flash_start = now
        return True

    # --- queries -------------------------------------------------------------
    def flashing(self, now: float) -> bool:
        if self._flash_start is None:
            return False
        if now - self._flash_start >= FLASH_PULSES * (FLASH_ON_S + FLASH_OFF_S):
            self._flash_start = None
            return False
        return True

    def idle_factor(self, now: float) -> float:
        """1.0 while the user is typing, falling to 0.0 over ``FADE_OUT_S``
        once the idle timeout has passed."""
        s = self.settings
        if s is None or not s.idle_timeout_s:
            return 1.0
        idle = now - self.last_activity
        if idle < s.idle_timeout_s:
            return 1.0
        return max(0.0, 1.0 - (idle - s.idle_timeout_s) / FADE_OUT_S)

    def is_dimmed(self, now: float) -> bool:
        """True once the keys have gone fully dark for lack of typing."""
        return self.idle_factor(now) == 0.0

    def look(self, now: float, wall: float) -> Look:
        s = self.settings
        if s is None or not s.enabled:
            return DARK
        if self.flashing(now):
            return self._flash_look(now)

        rgb = s.rgb
        factor = self.idle_factor(now)
        period = s.effective_period_s()
        if s.effect == CYCLE:
            rgb = cycle_rgb(wall, period)
        elif s.effect == TEMPERATURE:
            rgb = temperature_rgb(self.temp)

        full = pct_to_byte(s.brightness_pct)
        if s.effect == BREATHE and factor == 1.0 and full > 0:
            level = max(BREATHE_MIN_LEVEL, round(full * breathe_factor(wall, period)))
        else:
            level = round(full * factor)
        if level <= 0:
            return DARK
        return Look(True, rgb, level)

    def _flash_look(self, now: float) -> Look:
        into_pulse = (now - self._flash_start) % (FLASH_ON_S + FLASH_OFF_S)
        if into_pulse < FLASH_ON_S:
            level = max(FLASH_MIN_LEVEL, pct_to_byte(self.settings.brightness_pct))
            return Look(True, FLASH_RGB, level)
        return DARK

    def next_delay(self, now: float) -> float | None:
        """Seconds until the look can next change on its own, or None when it
        will hold until something outside happens (a key press, a setting, a
        temperature reading). The daemon sleeps exactly this long."""
        s = self.settings
        if s is None or not s.enabled:
            return None
        if self.flashing(now):
            into_pulse = (now - self._flash_start) % (FLASH_ON_S + FLASH_OFF_S)
            edge = FLASH_ON_S if into_pulse < FLASH_ON_S else FLASH_ON_S + FLASH_OFF_S
            return max(0.01, edge - into_pulse)

        delays: list[float] = []
        if s.idle_timeout_s:
            idle = now - self.last_activity
            if idle < s.idle_timeout_s:
                delays.append(s.idle_timeout_s - idle)
            elif idle < s.idle_timeout_s + FADE_OUT_S:
                return FADE_STEP_S  # mid-fade: the fade sets the pace
            else:
                return None  # dark: nothing changes until a key is pressed

        if s.effect == CYCLE:
            lo, hi = CYCLE_STEP_LIMITS_S
            step = s.effective_period_s() / CYCLE_STEPS_PER_LOOP
            delays.append(min(hi, max(lo, step)))
        elif s.effect == BREATHE:
            delays.append(BREATHE_STEP_S)
        # STATIC holds; TEMPERATURE is redrawn by the telemetry tick.
        return min(delays) if delays else None


# --- the hardware side -----------------------------------------------------------

class Driver:
    """Writes a ``Look`` to the EC, sending only what changed since the last
    one. ``shown`` is None when the EC's state is unknown (start-up, resume, a
    failed write), which forces a full master-enable + level + colour."""

    def __init__(self, ec) -> None:
        self.ec = ec
        self.shown: Look | None = None

    def invalidate(self) -> None:
        self.shown = None

    def show(self, look: Look) -> bool:
        """Drive the EC to ``look``. Returns True if anything was written. On a
        write failure the remembered state is dropped (we no longer know what
        the EC shows) and the exception propagates."""
        prev = self.shown
        if look == prev:
            return False
        try:
            if not look.on:
                if prev is None or prev.on:
                    keyboard.set_enabled(self.ec, False)
            elif prev is None or not prev.on:
                # From dark (or unknown): enable, then level before colour so the
                # keys never light up at a stale level for a frame.
                keyboard.set_enabled(self.ec, True)
                keyboard.write_level(self.ec, look.level)
                keyboard.write_color(self.ec, *look.rgb)
            else:
                if look.level != prev.level:
                    keyboard.write_level(self.ec, look.level)
                if look.rgb != prev.rgb:
                    keyboard.write_color(self.ec, *look.rgb)
        except Exception:
            self.shown = None
            raise
        self.shown = look
        return True
