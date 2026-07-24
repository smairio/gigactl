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
    """Coerce arbitrary points into a valid ``POINTS``-point curve: clamp to
    range, sort, reduce/pad to exactly ``POINTS`` points, then force temps to
    strictly increase (leaving headroom against the 100 ceiling) and duties to
    be non-decreasing. Idempotent on an already-valid curve."""
    pts = [(max(0, min(100, int(t))), max(0, min(100, int(d)))) for t, d in points]
    pts.sort(key=lambda p: p[0])
    if not pts:
        pts = [(40, FLOOR_PCT)]

    if len(pts) > POINTS:  # keep first, last, and evenly-spaced middles
        idx = sorted({round(i * (len(pts) - 1) / (POINTS - 1)) for i in range(POINTS)})
        pts = [pts[i] for i in idx]
    while len(pts) < POINTS:
        pts.append(pts[-1])

    # strictly-increasing temps, reserving room for the points that follow so
    # collisions near the top never pile up against the 100 ceiling
    spaced: list[Point] = []
    for i, (t, d) in enumerate(pts):
        lo = 0 if i == 0 else spaced[-1][0] + 1
        hi = 100 - (POINTS - 1 - i)
        spaced.append((min(max(t, lo), hi), d))

    # non-decreasing duties
    out: list[Point] = []
    last_d = 0
    for t, d in spaced:
        d = max(d, last_d)
        out.append((t, d))
        last_d = d
    return out


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

    def set_one_curve(self, which: str, points, linked: bool) -> None:
        """Update one fan's curve ('cpu'/'gpu'), preserving the other (or
        mirroring it when linked), and select the custom profile."""
        if which not in ("cpu", "gpu"):
            raise ValueError(f"unknown curve {which!r}; expected 'cpu' or 'gpu'")
        new = normalize([tuple(p) for p in points])
        cur_cpu = self._curves.get(CPU_FAN) or new
        cur_gpu = self._curves.get(GPU_FAN) or new
        if linked:
            cpu = gpu = new
        elif which == "cpu":
            cpu, gpu = new, cur_gpu
        else:
            cpu, gpu = cur_cpu, new
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

    def decide(self, fan: int, source_temp: int,
               cpu_temp: Optional[int] = None) -> Optional[int]:
        """Duty% to apply to ``fan`` given its source temp, or None to leave it.

        Hysteresis governs only the *curve* target (held across sub-4 °C moves).
        The safety floor is evaluated every tick regardless, and engages when
        either the fan's own component or the CPU is hot — so a fan can never
        sit below the floor while something is hot, even mid-hysteresis.
        """
        if self.profile in (FIRMWARE, MANUAL):
            return None
        if cpu_temp is None:
            cpu_temp = source_temp
        last_t = self._last_temp.get(fan)
        last_d = self._last_duty.get(fan)

        if last_t is None or abs(source_temp - last_t) >= self.hysteresis:
            curve_target = duty_for(self._curves[fan], source_temp)
            self._last_temp[fan] = source_temp
        else:  # small move: hold the curve target we last settled on
            curve_target = last_d if last_d is not None else duty_for(self._curves[fan], source_temp)

        target = apply_floor(curve_target, max(source_temp, cpu_temp),
                             self.floor_pct, self.floor_temp)
        if target == last_d:
            return None
        self._last_duty[fan] = target
        return target
