"""The lighting state machine and the diffing EC driver — pure timing rules
driven with explicit clocks, plus the writes the driver sends."""
import pytest

from gigactld import keyboard as kb
from gigactld import lighting as lt
from gigactld import mailbox


def _lit(**kw):
    defaults = dict(enabled=True, r=0, g=0, b=255, brightness_pct=100)
    defaults.update(kw)
    return lt.Lighting(kb.KeyboardState(**defaults))


# --- steady and off ----------------------------------------------------------------

def test_static_shows_the_chosen_colour_and_holds():
    L = _lit(r=10, g=20, b=30, brightness_pct=50)
    look = L.look(100.0, 100.0)
    assert look == lt.Look(True, (10, 20, 30), mailbox.pct_to_byte(50))
    assert L.next_delay(100.0) is None  # nothing to redraw on its own


def test_switched_off_is_dark_and_still():
    L = _lit(enabled=False)
    assert L.look(0.0, 0.0) == lt.DARK
    assert L.next_delay(0.0) is None


def test_no_settings_is_dark():
    L = lt.Lighting()
    assert L.look(0.0, 0.0) == lt.DARK
    assert L.next_delay(0.0) is None


def test_zero_brightness_is_dark():
    assert _lit(brightness_pct=0).look(0.0, 0.0) == lt.DARK


# --- idle fade ----------------------------------------------------------------------

def test_idle_waits_then_fades_then_holds_dark():
    L = _lit(idle_timeout_s=30)
    L.note_activity(1000.0)
    full = mailbox.pct_to_byte(100)

    assert L.look(1010.0, 0).level == full
    assert L.next_delay(1010.0) == pytest.approx(20.0)  # sleep until the deadline

    halfway = 1000.0 + 30 + lt.FADE_OUT_S / 2
    assert L.look(halfway, 0).level == pytest.approx(full / 2, abs=1)
    assert L.next_delay(halfway) == lt.FADE_STEP_S  # mid-fade: step quickly

    dark = 1000.0 + 30 + lt.FADE_OUT_S
    assert L.look(dark, 0) == lt.DARK
    assert L.is_dimmed(dark)
    assert L.next_delay(dark) is None  # nothing until a key is pressed


def test_key_press_wakes_instantly_and_says_so():
    L = _lit(idle_timeout_s=10)
    L.note_activity(0.0)
    assert L.look(100.0, 0) == lt.DARK
    assert L.note_activity(100.0) is True  # caller must redraw now
    assert L.look(100.0, 0).level == mailbox.pct_to_byte(100)
    assert L.note_activity(101.0) is False  # already lit: nothing to redraw


def test_no_idle_timeout_never_fades():
    L = _lit(idle_timeout_s=0)
    L.note_activity(0.0)
    assert L.look(10_000.0, 0).on
    assert not L.is_dimmed(10_000.0)


# --- effects ------------------------------------------------------------------------

def test_cycle_walks_the_colour_wheel_by_wall_clock():
    L = _lit(effect=kb.CYCLE, period_s=60)
    assert L.look(0, 0.0).rgb == (255, 0, 0)        # hue 0
    assert L.look(0, 20.0).rgb == (0, 255, 0)       # a third of the way
    assert L.look(0, 40.0).rgb == (0, 0, 255)       # two thirds
    assert L.look(0, 60.0).rgb == (255, 0, 0)       # full loop


def test_cycle_step_is_clamped_both_ways():
    fast = _lit(effect=kb.CYCLE, period_s=10)
    slow = _lit(effect=kb.CYCLE, period_s=24 * 3600)
    assert fast.next_delay(0) == lt.CYCLE_STEP_LIMITS_S[0]
    assert slow.next_delay(0) == lt.CYCLE_STEP_LIMITS_S[1]


def test_cycle_default_speed_when_none_chosen():
    L = _lit(effect=kb.CYCLE, period_s=0)
    expected = kb.DEFAULT_PERIOD_S[kb.CYCLE] / lt.CYCLE_STEPS_PER_LOOP
    assert L.next_delay(0) == pytest.approx(expected)


def test_breathe_keeps_colour_and_never_hits_zero():
    L = _lit(effect=kb.BREATHE, period_s=4, r=1, g=2, b=3)
    trough = L.look(0, 0.0)
    peak = L.look(0, 2.0)
    assert trough.on and trough.level == lt.BREATHE_MIN_LEVEL
    assert peak.level == mailbox.pct_to_byte(100)
    assert trough.rgb == peak.rgb == (1, 2, 3)
    assert L.next_delay(0) == lt.BREATHE_STEP_S


def test_breathe_fades_out_when_idle():
    L = _lit(effect=kb.BREATHE, idle_timeout_s=5)
    L.note_activity(0.0)
    assert L.look(5 + lt.FADE_OUT_S, 2.0) == lt.DARK


def test_temperature_colour_endpoints_and_blend():
    assert lt.temperature_rgb(None) == (0, 0, 255)
    assert lt.temperature_rgb(20) == (0, 0, 255)
    assert lt.temperature_rgb(100) == (255, 0, 0)
    mid = lt.temperature_rgb(52.5)  # halfway blue -> green
    assert mid == (0, 128, 128)


def test_temperature_is_smoothed():
    L = _lit(effect=kb.TEMPERATURE)
    L.note_temperature(40)
    L.note_temperature(95)
    assert 40 < L.temp < 95  # a spike moves it part of the way


def test_temperature_effect_waits_for_telemetry():
    assert _lit(effect=kb.TEMPERATURE).next_delay(0) is None


# --- flash --------------------------------------------------------------------------

def test_flash_blinks_white_then_returns_to_normal():
    L = _lit(r=0, g=0, b=255, brightness_pct=10)
    assert L.start_flash(0.0)
    on = L.look(0.05, 0)
    assert on.rgb == lt.FLASH_RGB and on.level == lt.FLASH_MIN_LEVEL  # dim user still sees it
    assert L.look(lt.FLASH_ON_S + 0.05, 0) == lt.DARK
    end = lt.FLASH_PULSES * (lt.FLASH_ON_S + lt.FLASH_OFF_S) + 0.01
    assert L.look(end, 0).rgb == (0, 0, 255)


def test_flash_reaches_dimmed_keys_and_leaves_them_dark():
    L = _lit(idle_timeout_s=5)
    L.note_activity(0.0)
    assert L.look(100.0, 0) == lt.DARK
    assert L.start_flash(100.0)
    assert L.look(100.05, 0).on
    assert L.look(102.0, 0) == lt.DARK  # flash is not typing


def test_no_flash_when_off_or_already_flashing():
    assert not _lit(enabled=False).start_flash(0.0)
    L = _lit()
    assert L.start_flash(0.0)
    assert not L.start_flash(0.1)


def test_flash_schedules_its_own_edges():
    L = _lit()
    L.start_flash(0.0)
    assert L.next_delay(0.0) == pytest.approx(lt.FLASH_ON_S)


# --- driver -------------------------------------------------------------------------

class _Ec:
    def __init__(self, fail=False):
        self.writes = []
        self.fail = fail

    def write_u8(self, off, val):
        if self.fail:
            raise OSError("EC busy")
        self.writes.append((off, val))

    def transaction(self):
        from contextlib import nullcontext
        return nullcontext()


def _commands(ec):
    """(sub, doorbell) per mailbox command, in order."""
    out, sub = [], None
    for off, val in ec.writes:
        if off == mailbox.FDAT:
            sub = val
        elif off == mailbox.FCMD:
            out.append((sub, val))
    return out


ENABLE = (kb.ENABLE_SUB, kb.DOORBELL_ENABLE)
LEVEL = (kb.SUB_BRIGHT, kb.DOORBELL_KB)
COLOUR = (kb.SUB_COLOR, kb.DOORBELL_KB)


def test_driver_first_frame_writes_everything_in_order():
    ec = _Ec()
    d = lt.Driver(ec)
    assert d.show(lt.Look(True, (1, 2, 3), 200))
    assert _commands(ec) == [ENABLE, LEVEL, COLOUR]


def test_driver_sends_only_what_changed():
    ec = _Ec()
    d = lt.Driver(ec)
    d.show(lt.Look(True, (1, 2, 3), 200))
    ec.writes.clear()
    assert not d.show(lt.Look(True, (1, 2, 3), 200))
    assert ec.writes == []
    d.show(lt.Look(True, (1, 2, 3), 150))
    assert _commands(ec) == [LEVEL]  # a fade step is one command, no re-enable
    ec.writes.clear()
    d.show(lt.Look(True, (9, 9, 9), 150))
    assert _commands(ec) == [COLOUR]


def test_driver_dark_disables_once_and_relights_fully():
    ec = _Ec()
    d = lt.Driver(ec)
    d.show(lt.Look(True, (1, 2, 3), 200))
    ec.writes.clear()
    d.show(lt.DARK)
    d.show(lt.DARK)
    assert _commands(ec) == [ENABLE]  # the disable, once
    assert (mailbox.FBUF, kb.ENABLE_OFF) in ec.writes
    ec.writes.clear()
    d.show(lt.Look(True, (1, 2, 3), 200))
    assert _commands(ec) == [ENABLE, LEVEL, COLOUR]


def test_driver_forgets_on_failure():
    ec = _Ec(fail=True)
    d = lt.Driver(ec)
    d.shown = lt.Look(True, (1, 2, 3), 200)
    with pytest.raises(OSError):
        d.show(lt.Look(True, (1, 2, 3), 100))
    assert d.shown is None  # next frame rewrites everything


def test_invalidate_forces_full_rewrite():
    ec = _Ec()
    d = lt.Driver(ec)
    look = lt.Look(True, (1, 2, 3), 200)
    d.show(look)
    d.invalidate()
    ec.writes.clear()
    d.show(look)
    assert _commands(ec) == [ENABLE, LEVEL, COLOUR]
