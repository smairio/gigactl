"""Curve-editor maths, kept GTK-free so it can be tested without a display.

The daemon owns the real curve engine; this module only needs to agree with it
closely enough that the editor never shows the user something the daemon would
quietly change:

* ``duty_for``/``predicted`` mirror the daemon's piecewise-linear lookup and its
  safety floor — including the floor's CPU cross-check, so the annotation cannot
  promise a duty the daemon will override;
* ``move_point`` enforces the same monotonic rules the daemon's ``normalize``
  enforces (strictly rising temperatures, non-falling duties) *while dragging*,
  rather than letting the user draw a shape that gets silently rewritten;
* ``seeded_from`` passes an already-valid curve through **untouched**. Only a
  malformed one is coerced, and then by the daemon's own rules.

Constants are duplicated from ``gigactld.curve`` on purpose: the two packages
install separately, so this is a wire contract, not shared code. They must be
changed together.
"""
from __future__ import annotations

POINTS = 5
FLOOR_PCT = 30
FLOOR_TEMP_C = 85

# The graph's drawn range. It starts at 30 °C to match the temperature scale's
# own floor (``temperature.GAUGE_MIN_C``) — below that the fans have nothing to
# do. The *daemon* accepts a first point as low as DAEMON_TEMP_MIN; such a curve
# can only come from a hand-made D-Bus call, and the editor draws it clipped at
# the left edge rather than rewriting it (see ``seeded_from``).
TEMP_MIN = 30
TEMP_MAX = 100
DAEMON_TEMP_MIN = 0  # gigactld.curve.normalize's lower bound for the first point

Point = tuple[int, int]
Curve = list[Point]

# Where a brand-new custom curve starts: a gentle ramp that is quieter than
# firmware at idle and reaches full speed before the throttling point.
DEFAULT_SEED: Curve = [(40, 30), (55, 40), (70, 60), (85, 80), (95, 100)]

_HIT_RADIUS_PX = 16


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def duty_for(points: Curve, temp: float) -> int:
    """Piecewise-linear duty for ``temp``, clamped at the curve's ends —
    mirrors ``gigactld.curve.duty_for``."""
    if not points:
        return 0
    if temp <= points[0][0]:
        return points[0][1]
    if temp >= points[-1][0]:
        return points[-1][1]
    for (t0, d0), (t1, d1) in zip(points, points[1:]):
        if t0 <= temp <= t1:
            span = t1 - t0
            if span == 0:
                return d1
            return round(d0 + (d1 - d0) * (temp - t0) / span)
    return points[-1][1]


def predicted(points: Curve, temp: float, cpu_temp: float | None = None) -> int:
    """What the fans will actually run at — the curve *plus* the daemon's floor.

    The daemon engages the floor on ``max(source_temp, cpu_temp)``, so a GPU
    curve is floored by a hot CPU too; pass ``cpu_temp`` to model that, or leave
    it out when the curve's own fan *is* the CPU.
    """
    duty = duty_for(points, temp)
    hottest = temp if cpu_temp is None else max(temp, cpu_temp)
    if hottest >= FLOOR_TEMP_C:
        return max(duty, FLOOR_PCT)
    return duty


def annotation(label: str, temp: int, points: Curve,
               cpu_temp: float | None = None) -> str:
    """The live read-out, e.g. ``"CPU now 66° → fans 45%"``."""
    return f"{label} now {temp}° → fans {predicted(points, temp, cpu_temp)}%"


def is_valid(points: Curve) -> bool:
    """True when the daemon would keep this curve as-is: the right number of
    points, temperatures strictly rising, duties never falling, duties in range.
    """
    if len(points) != POINTS:
        return False
    if any(not 0 <= d <= 100 for _, d in points):
        return False
    if any(t2 <= t1 for (t1, _), (t2, _) in zip(points, points[1:])):
        return False
    return all(d2 >= d1 for (_, d1), (_, d2) in zip(points, points[1:]))


def move_point(points: Curve, index: int, temp: float, duty: float) -> Curve:
    """``points`` with point ``index`` dragged to (temp, duty), clamped so the
    curve stays valid: each temperature strictly between its neighbours', each
    duty between theirs. The first point may go as low as the drawn range."""
    if not 0 <= index < len(points):
        raise IndexError(f"no point {index} in a {len(points)}-point curve")

    lo_t = TEMP_MIN if index == 0 else points[index - 1][0] + 1
    hi_t = TEMP_MAX if index == len(points) - 1 else points[index + 1][0] - 1
    lo_d = 0 if index == 0 else points[index - 1][1]
    hi_d = 100 if index == len(points) - 1 else points[index + 1][1]

    moved = list(points)
    moved[index] = (int(round(_clamp(temp, lo_t, max(lo_t, hi_t)))),
                    int(round(_clamp(duty, lo_d, max(lo_d, hi_d)))))
    return moved


def seeded_from(points: Curve | None) -> Curve:
    """The shape to open the editor with: whatever the daemon is driving, or the
    default ramp when it is driving nothing (firmware/manual report no points).

    A valid curve is returned **untouched**, even if it starts colder than the
    drawn range — rewriting it here would mean the editor showed, and on the next
    edit sent, a shape the user never chose.
    """
    if not points:
        return list(DEFAULT_SEED)
    exact = [(int(t), int(d)) for t, d in points]
    return exact if is_valid(exact) else _normalise(exact)


def _normalise(points: Curve) -> Curve:
    """Coerce a malformed curve to exactly ``POINTS`` rising points, by the
    daemon's rules (``gigactld.curve.normalize``) so both ends agree — note the
    first point's lower bound is the daemon's, not the drawn range's."""
    pts = sorted(points)
    while len(pts) < POINTS:  # spread duplicates rather than inventing a shape
        pts.append(pts[-1])
    if len(pts) > POINTS:
        step = (len(pts) - 1) / (POINTS - 1)
        pts = [pts[round(i * step)] for i in range(POINTS)]

    out: Curve = []
    last_d = 0
    for i, (t, d) in enumerate(pts):
        lo = DAEMON_TEMP_MIN if i == 0 else out[-1][0] + 1
        t = int(_clamp(t, lo, TEMP_MAX - (POINTS - 1 - i)))
        d = int(_clamp(max(d, last_d), 0, 100))
        out.append((t, d))
        last_d = d
    return out


# --- graph mapping ----------------------------------------------------------

def to_pixel(temp: float, duty: float, width: float, height: float) -> tuple[float, float]:
    """(temp, duty) → (x, y) with the origin bottom-left: cold+slow at the
    bottom-left, hot+fast at the top-right. A point colder than ``TEMP_MIN``
    maps to a negative x on purpose, so it draws clipped instead of moving."""
    x = (temp - TEMP_MIN) / (TEMP_MAX - TEMP_MIN) * width
    y = height - (duty / 100) * height
    return (x, y)


def from_pixel(x: float, y: float, width: float, height: float) -> tuple[float, float]:
    """(x, y) → (temp, duty). A zero-sized area (before the first allocation)
    has no meaningful mapping, so it answers with the bottom-left corner rather
    than dividing by zero inside a gesture callback."""
    if width <= 0 or height <= 0:
        return (float(TEMP_MIN), 0.0)
    temp = TEMP_MIN + _clamp(x / width, 0.0, 1.0) * (TEMP_MAX - TEMP_MIN)
    duty = _clamp((height - y) / height, 0.0, 1.0) * 100
    return (temp, duty)


def hit_test(points: Curve, x: float, y: float, width: float, height: float,
             radius: float = _HIT_RADIUS_PX) -> int | None:
    """Index of the point under the pointer, or None. Nearest wins, so points
    that crowd together near the top of the curve stay grabbable."""
    best: tuple[float, int] | None = None
    for i, (temp, duty) in enumerate(points):
        px, py = to_pixel(temp, duty, width, height)
        distance = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
        if distance <= radius and (best is None or distance < best[0]):
            best = (distance, i)
    return None if best is None else best[1]
