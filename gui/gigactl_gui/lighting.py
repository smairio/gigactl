"""Keyboard effects and the auto-off timer, as the window offers them.

GTK-free, like ``backlight`` and ``temperature``, so the choices and wording
are unit-tested without a display. The effect names, speeds and limits mirror
the daemon's ``keyboard`` module — the daemon validates every request, so a
mismatch here would surface as a refused write, never a wrong one.
"""
from __future__ import annotations

from dataclasses import dataclass

STATIC = "static"
CYCLE = "cycle"
BREATHE = "breathe"
TEMPERATURE = "temperature"


@dataclass(frozen=True)
class Effect:
    id: str
    label: str
    subtitle: str


EFFECTS: tuple[Effect, ...] = (
    Effect(STATIC, "Static", "one colour"),
    Effect(CYCLE, "Colour cycle", "slow rainbow"),
    Effect(BREATHE, "Breathing", "fades in and out"),
    Effect(TEMPERATURE, "CPU heat", "blue → red"),
)

# (seconds per loop, label). The first default-marked one is what the daemon
# uses when none is chosen (period 0).
SPEEDS: dict[str, tuple[tuple[int, str], ...]] = {
    CYCLE: (
        (30, "Fast — every 30 seconds"),
        (60, "Every minute"),
        (300, "Every 5 minutes"),
        (900, "Every 15 minutes"),
        (3600, "Every hour"),
    ),
    BREATHE: (
        (3, "Fast — 3 seconds"),
        (6, "Calm — 6 seconds"),
        (10, "Slow — 10 seconds"),
    ),
}
DEFAULT_SPEED = {CYCLE: 300, BREATHE: 6}

# Turn the keys off after this long without typing. 0 = never.
IDLE_CHOICES: tuple[int, ...] = (0, 10, 30, 60, 120, 300, 600, 1800)


def effect_label(effect_id: str) -> str:
    for e in EFFECTS:
        if e.id == effect_id:
            return e.label
    return effect_id.capitalize()


def duration_text(seconds: int) -> str:
    """Plain words for a duration (PRODUCT.md: simple English)."""
    if seconds <= 0:
        return "Never"
    if seconds % 3600 == 0:
        h = seconds // 3600
        return f"{h} hour" + ("s" if h != 1 else "")
    if seconds % 60 == 0:
        m = seconds // 60
        return f"{m} minute" + ("s" if m != 1 else "")
    if seconds > 60:
        m, s = divmod(seconds, 60)
        return f"{m} min {s} s"
    return f"{seconds} seconds"


def idle_choices(current: int) -> list[int]:
    """The auto-off menu. A value set elsewhere (``gkbd idle 45``) is added in
    order, so the menu can always show what the daemon is really doing."""
    choices = list(IDLE_CHOICES)
    if current not in choices:
        choices.append(current)
        choices.sort()
    return choices


def speed_choices(effect: str, current: int) -> list[tuple[int, str]]:
    """The speed menu for an effect, likewise keeping an off-menu value."""
    choices = list(SPEEDS.get(effect, ()))
    if choices and current and current not in {s for s, _ in choices}:
        choices.append((current, f"Every {duration_text(current).lower()}"))
        choices.sort()
    return choices
