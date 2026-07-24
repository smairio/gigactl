"""Fan curve model and the automatic profile engine.

A curve is 5 monotonic ``(temp_celsius, duty_percent)`` points. The engine maps
a fan's source temperature to a duty each tick, with hysteresis (don't react to
tiny wiggles) and a safety floor (never idle a fan while its component is hot).

Everything here is pure: it reads no hardware and holds no D-Bus. The daemon
feeds it temperatures and applies the duties it returns.
"""
from __future__ import annotations

from typing import Optional

CPU_FAN = 1
GPU_FAN = 2
FANS = (CPU_FAN, GPU_FAN)

POINTS = 5
HYSTERESIS_C = 4
FLOOR_PCT = 30
FLOOR_TEMP_C = 85

Point = tuple[int, int]
Curve = list[Point]

# Built-in profiles: 5 points each, temps increasing, duties non-decreasing.
PROFILES: dict[str, Curve] = {
    "quiet":       [(40, 20), (60, 25), (75, 40), (85, 60), (95, 100)],
    "balanced":    [(40, 25), (55, 35), (70, 55), (85, 80), (95, 100)],
    "performance": [(40, 40), (55, 55), (70, 75), (85, 95), (95, 100)],
    "max":         [(40, 100), (55, 100), (70, 100), (85, 100), (95, 100)],
}
FIRMWARE = "firmware"   # fans handed to the firmware curve; engine drives nothing
CUSTOM = "custom"
MANUAL = "manual"       # a manual SetFanDuty is in effect; engine leaves fans alone


def duty_for(curve: Curve, temp: int) -> int:
    """Piecewise-linear duty for ``temp``, clamped at the curve's ends."""
    if temp <= curve[0][0]:
        return curve[0][1]
    if temp >= curve[-1][0]:
        return curve[-1][1]
    for (t0, d0), (t1, d1) in zip(curve, curve[1:]):
        if t0 <= temp <= t1:
            span = t1 - t0
            if span == 0:
                return d1
            return round(d0 + (d1 - d0) * (temp - t0) / span)
    return curve[-1][1]  # unreachable given the clamps above


def apply_floor(duty: int, temp: int, floor_pct: int = FLOOR_PCT,
                floor_temp: int = FLOOR_TEMP_C) -> int:
    """Never let a fan drop below ``floor_pct`` while its source temp is hot."""
    if temp >= floor_temp:
        return max(duty, floor_pct)
    return duty


def normalize(points: list[Point]) -> Curve:
    """Coerce arbitrary points into a valid 5-point curve: clamp to range,
    sort by temp, dedupe/space temps so they strictly increase, force duties
    non-decreasing, then pad or truncate to exactly ``POINTS`` points."""
    pts = [(max(0, min(100, int(t))), max(0, min(100, int(d)))) for t, d in points]
    pts.sort(key=lambda p: p[0])

    # strictly increasing temps: bump any that collide with the previous
    spaced: list[Point] = []
    for t, d in pts:
        if spaced and t <= spaced[-1][0]:
            t = min(100, spaced[-1][0] + 1)
        spaced.append((t, d))

    # non-decreasing duties
    mono: list[Point] = []
    last_d = 0
    for t, d in spaced:
        d = max(d, last_d)
        mono.append((t, d))
        last_d = d

    if not mono:
        mono = [(40, FLOOR_PCT)]
    # pad to POINTS by repeating the last point (temps still strictly increase)
    while len(mono) < POINTS:
        t = min(100, mono[-1][0] + 1)
        mono.append((t, mono[-1][1]))
    if len(mono) > POINTS:
        # keep first, last, and evenly-spaced middles
        idx = [round(i * (len(mono) - 1) / (POINTS - 1)) for i in range(POINTS)]
        mono = [mono[i] for i in sorted(set(idx))]
        while len(mono) < POINTS:  # dedupe may shrink; re-pad
            t = min(100, mono[-1][0] + 1)
            mono.append((t, mono[-1][1]))
    return mono


class ProfileEngine:
    """Holds the active profile and per-fan hysteresis state; turns a source
    temperature into the duty to apply (or ``None`` to leave the fan alone)."""

    def __init__(self, hysteresis: int = HYSTERESIS_C,
                 floor_pct: int = FLOOR_PCT, floor_temp: int = FLOOR_TEMP_C) -> None:
        self.hysteresis = hysteresis
        self.floor_pct = floor_pct
        self.floor_temp = floor_temp
        self.profile = FIRMWARE
        self.linked = True
        self._curves: dict[int, Curve] = {}
        self._last_temp: dict[int, Optional[int]] = {}
        self._last_duty: dict[int, Optional[int]] = {}

    def _reset_state(self) -> None:
        self._last_temp = {f: None for f in FANS}
        self._last_duty = {f: None for f in FANS}

    def set_profile(self, name: str) -> None:
        if name == FIRMWARE:
            self.profile = FIRMWARE
            self._curves = {}
        elif name in PROFILES:
            self.profile = name
            self._curves = {f: list(PROFILES[name]) for f in FANS}
        elif name == CUSTOM:
            if not self._curves:
                raise ValueError("no custom curve set yet")
            self.profile = CUSTOM
        else:
            raise ValueError(f"unknown profile {name!r}")
        self._reset_state()

    def set_custom(self, cpu_points, gpu_points=None, linked: bool = True) -> None:
        cpu = normalize(cpu_points)
        gpu = cpu if linked or gpu_points is None else normalize(gpu_points)
        self.linked = linked
        self._curves = {CPU_FAN: cpu, GPU_FAN: gpu}
        self.profile = CUSTOM
        self._reset_state()

    def set_manual(self) -> None:
        """A manual duty override is in effect; the engine stops driving until
        a profile is (re)selected."""
        self.profile = MANUAL
        self._curves = {}
        self._reset_state()

    def curve_for(self, fan: int) -> Curve:
        return self._curves.get(fan, [])

    def decide(self, fan: int, source_temp: int) -> Optional[int]:
        """Duty% to apply to ``fan`` given its source temp, or None to leave it.

        Returns None when driving firmware, when the temp hasn't moved past the
        hysteresis band since the last decision, or when the target is unchanged.
        """
        if self.profile in (FIRMWARE, MANUAL):
            return None
        last_t = self._last_temp.get(fan)
        if last_t is not None and abs(source_temp - last_t) < self.hysteresis:
            return None
        target = apply_floor(duty_for(self._curves[fan], source_temp), source_temp,
                             self.floor_pct, self.floor_temp)
        self._last_temp[fan] = source_temp
        if target == self._last_duty.get(fan):
            return None
        self._last_duty[fan] = target
        return target
