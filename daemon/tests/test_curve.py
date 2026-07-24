"""Curve model + profile engine — pure logic, TDD'd with no hardware."""
import pytest

from gigactld import curve


# --- duty_for: piecewise-linear interpolation -------------------------------
def test_duty_for_clamps_below_first_and_above_last():
    c = [(40, 20), (60, 40), (80, 80), (90, 100), (100, 100)]
    assert curve.duty_for(c, 20) == 20    # below first point -> first duty
    assert curve.duty_for(c, 110) == 100  # above last -> last duty


def test_duty_for_interpolates_between_points():
    c = [(40, 20), (60, 40), (80, 80), (90, 100), (100, 100)]
    assert curve.duty_for(c, 50) == 30    # halfway 40->60, 20->40
    assert curve.duty_for(c, 70) == 60    # halfway 60->80, 40->80


# --- normalize: enforce 5 strictly-increasing temps, non-decreasing duties --
def test_normalize_sorts_dedupes_clamps_and_pads_to_five():
    raw = [(60, 50), (40, 80), (40, 10), (200, 999), (-5, -5)]
    n = curve.normalize(raw)
    temps = [t for t, _ in n]
    duties = [d for _, d in n]
    assert len(n) == 5
    assert temps == sorted(set(temps))            # strictly increasing, no dupes
    assert all(0 <= t <= 100 for t in temps)
    assert all(0 <= d <= 100 for d in duties)
    assert duties == sorted(duties)               # non-decreasing (monotonic)


def test_normalize_is_idempotent():
    once = curve.normalize([(40, 20), (55, 35), (70, 55), (85, 80), (95, 100)])
    assert curve.normalize(once) == once


# --- floor -------------------------------------------------------------------
def test_apply_floor_only_bites_at_or_above_floor_temp():
    assert curve.apply_floor(10, temp=86, floor_pct=30, floor_temp=85) == 30
    assert curve.apply_floor(10, temp=80, floor_pct=30, floor_temp=85) == 10
    assert curve.apply_floor(55, temp=90, floor_pct=30, floor_temp=85) == 55  # already above floor


# --- profiles ----------------------------------------------------------------
def test_builtin_profiles_are_valid_curves():
    for name in ("quiet", "balanced", "performance", "max"):
        c = curve.PROFILES[name]
        assert len(c) == 5
        assert [t for t, _ in c] == sorted(t for t, _ in c)
        assert [d for _, d in c] == sorted(d for _, d in c)


# --- ProfileEngine.decide ----------------------------------------------------
def test_engine_max_profile_commands_full_then_holds():
    e = curve.ProfileEngine()
    e.set_profile("max")
    assert e.decide(curve.CPU_FAN, 50) == 100
    assert e.decide(curve.CPU_FAN, 51) is None  # unchanged -> nothing to write


def test_engine_firmware_profile_drives_nothing():
    e = curve.ProfileEngine()
    e.set_profile("firmware")
    assert e.decide(curve.CPU_FAN, 95) is None


def test_engine_hysteresis_suppresses_small_moves():
    e = curve.ProfileEngine(hysteresis=4)
    e.set_profile("balanced")
    assert e.decide(curve.CPU_FAN, 60) is not None   # first decision applies
    assert e.decide(curve.CPU_FAN, 62) is None        # +2C < hysteresis
    assert e.decide(curve.CPU_FAN, 66) is not None    # +6C from last decision -> re-decide


def test_engine_applies_floor_at_high_temp():
    e = curve.ProfileEngine(floor_pct=30, floor_temp=85)
    e.set_profile("quiet")
    # whatever quiet says at 90C, the floor guarantees >= 30
    assert e.decide(curve.CPU_FAN, 90) >= 30


def test_engine_set_profile_resets_hysteresis_state():
    e = curve.ProfileEngine()
    e.set_profile("balanced")
    e.decide(curve.CPU_FAN, 60)
    e.set_profile("performance")          # switching must re-apply immediately
    assert e.decide(curve.CPU_FAN, 60) is not None


def test_engine_set_custom_normalizes_and_selects_custom():
    e = curve.ProfileEngine()
    e.set_custom(cpu_points=[(90, 10), (40, 90)], gpu_points=None, linked=True)
    assert e.profile == "custom"
    # linked -> GPU uses the same (normalized, monotonic) curve as CPU
    assert e.decide(curve.CPU_FAN, 50) == e.decide(curve.GPU_FAN, 50)


def test_engine_unknown_profile_raises():
    e = curve.ProfileEngine()
    with pytest.raises(ValueError):
        e.set_profile("turbo")


def test_engine_manual_mode_drives_nothing():
    e = curve.ProfileEngine()
    e.set_profile("balanced")
    e.set_manual()                       # a manual SetFanDuty took over
    assert e.profile == curve.MANUAL
    assert e.decide(curve.CPU_FAN, 95) is None


def test_floor_holds_on_small_move_into_hot_zone():
    # the review bug: hysteresis must NOT suppress the safety floor
    e = curve.ProfileEngine(hysteresis=4, floor_pct=30, floor_temp=85)
    e.set_custom(cpu_points=[(40, 10), (95, 15)], gpu_points=None, linked=True)
    e.decide(curve.CPU_FAN, 83)                 # cool-ish, low duty
    d = e.decide(curve.CPU_FAN, 86)             # +3C (< hysteresis) into the hot zone
    assert d is not None and d >= 30            # floor engages anyway


def test_floor_engages_from_cpu_temp_for_gpu_fan():
    # GPU cool but CPU hot -> the GPU fan is still floored (max of the two temps)
    e = curve.ProfileEngine(floor_pct=30, floor_temp=85)
    e.set_custom(cpu_points=[(40, 10), (95, 15)], gpu_points=None, linked=True)
    assert e.decide(curve.GPU_FAN, 50, cpu_temp=90) >= 30


def test_normalize_strict_temps_against_ceiling():
    # adversarial: everything piled at the top must still come out strictly increasing
    n = curve.normalize([(100, 40), (100, 90), (100, 10), (99, 50), (100, 100)])
    temps = [t for t, _ in n]
    assert len(temps) == 5
    assert temps == sorted(set(temps))          # strictly increasing, no dupes
    assert all(0 <= t <= 100 for t in temps)


def test_set_one_curve_preserves_the_other_fan_when_split():
    e = curve.ProfileEngine()
    e.set_one_curve("cpu", [(40, 30), (95, 100)], linked=True)   # both = steep
    before_gpu = list(e.curve_for(curve.GPU_FAN))
    e.set_one_curve("cpu", [(40, 60), (95, 100)], linked=False)  # change CPU only
    assert e.curve_for(curve.GPU_FAN) == before_gpu             # GPU untouched
    assert e.curve_for(curve.CPU_FAN) != before_gpu
