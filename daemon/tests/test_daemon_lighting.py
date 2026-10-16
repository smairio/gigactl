"""Daemon-level lighting: the new D-Bus methods, the idle fade driven through
the real timer path, temperature colour on the telemetry tick, and rollback when
the EC refuses. Time is faked so nothing sleeps."""
import gi
import pytest

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from conftest import Invocation  # noqa: E402
from gigactld import keyboard as kb  # noqa: E402
from gigactld import lighting as lt  # noqa: E402
from gigactld import mailbox, service, state  # noqa: E402


class _Clock:
    def __init__(self):
        self.mono = 1000.0
        self.wall = 0.0

    def monotonic(self):
        return self.mono

    def time(self):
        return self.wall

    def sleep(self, _s):
        pass


class _FakeWatcher:
    def __init__(self):
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


@pytest.fixture
def daemon(make_daemon, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(service, "time", clock)
    d = make_daemon()
    d.activity = _FakeWatcher()
    d.clock = clock
    yield d
    if d._light_source:
        GLib.source_remove(d._light_source)


def _call(d, method, sig=None, *args):
    inv = Invocation()
    params = GLib.Variant(sig, args) if sig else None
    d._method_call(None, ":1.1", None, None, method, params, inv)
    return inv


def _levels(d):
    out, sub = [], None
    for off, val in d.ec.writes:
        if off == mailbox.FDAT:
            sub = val
        elif off == mailbox.FBUF and sub == kb.SUB_BRIGHT:
            out.append(val)
    return out


def _fire_timer(d):
    assert d._light_source, "expected a pending lighting timer"
    GLib.source_remove(d._light_source)
    d._on_light_timer()


def test_idle_timeout_fades_to_dark_and_a_key_wakes_it(daemon):
    d = daemon
    assert _call(d, "SetKeyboardIdleTimeout", "(u)", 30).returned
    assert d.activity.running  # watching keyboards only now
    assert state.load(d.state_path)["keyboard"]["idle_timeout"] == 30

    d.clock.mono += 31  # past the deadline: the fade starts
    d.ec.writes.clear()
    _fire_timer(d)
    for _ in range(40):
        if not d._light_source:
            break
        d.clock.mono += lt.FADE_STEP_S
        _fire_timer(d)
    levels = _levels(d)
    assert levels == sorted(levels, reverse=True)  # strictly fading down
    assert (mailbox.FBUF, kb.ENABLE_OFF) in d.ec.writes  # ends master-disabled
    assert d._kbd_dimmed
    changed = d._conn.properties_changed()
    assert changed[1].get("KeyboardIdle") is True

    d.ec.writes.clear()
    d._on_key_activity()
    assert (mailbox.FBUF, kb.ENABLE_ON) in d.ec.writes  # lit again at once
    assert not d._kbd_dimmed


def test_turning_idle_off_stops_watching(daemon):
    d = daemon
    _call(d, "SetKeyboardIdleTimeout", "(u)", 60)
    assert d.activity.running
    _call(d, "SetKeyboardIdleTimeout", "(u)", 0)
    assert not d.activity.running


def test_switching_light_off_stops_watching(daemon):
    d = daemon
    _call(d, "SetKeyboardIdleTimeout", "(u)", 60)
    _call(d, "SetKeyboardEnabled", "(b)", False)
    assert not d.activity.running


def test_bad_idle_timeout_is_refused(daemon):
    inv = _call(daemon, "SetKeyboardIdleTimeout", "(u)", 2)
    assert inv.error[0].endswith("InvalidArgs")
    assert daemon.keyboard is None  # nothing created by a refused call


def test_effect_is_validated_stored_and_announced(daemon):
    d = daemon
    assert _call(d, "SetKeyboardEffect", "(su)", "cycle", 600).returned
    assert d.keyboard.effect == kb.CYCLE and d.keyboard.period_s == 600
    eff = d._conn.properties_changed()[1]["KeyboardEffects"]
    assert eff == ("cycle", 600, 0, False)
    assert d._light_source  # the cycle keeps itself ticking

    assert _call(d, "SetKeyboardEffect", "(su)", "disco", 0).error[0].endswith("InvalidArgs")
    assert _call(d, "SetKeyboardEffect", "(su)", "breathe", 1).error[0].endswith("InvalidArgs")


def test_effects_property_reports_default_speed(daemon):
    d = daemon
    _call(d, "SetKeyboardEffect", "(su)", "breathe", 0)
    v = d._get_property(None, None, None, None, "KeyboardEffects").unpack()
    assert v == ("breathe", kb.DEFAULT_PERIOD_S[kb.BREATHE], 0, False)


def test_picking_a_colour_leaves_cycle_for_static(daemon):
    d = daemon
    _call(d, "SetKeyboardEffect", "(su)", "cycle", 0)
    _call(d, "SetKeyboardColor", "(uuu)", 255, 0, 0)
    assert d.keyboard.effect == kb.STATIC


def test_picking_a_colour_keeps_breathing(daemon):
    d = daemon
    _call(d, "SetKeyboardEffect", "(su)", "breathe", 0)
    _call(d, "SetKeyboardColor", "(uuu)", 255, 0, 0)
    assert d.keyboard.effect == kb.BREATHE


def test_temperature_effect_follows_telemetry(daemon, monkeypatch):
    d = daemon
    _call(d, "SetKeyboardEffect", "(su)", "temperature", 0)
    hot = type("T", (), dict(cpu_temp=95, gpu_temp=60, fan1_rpm=0, fan2_rpm=0,
                             fan1_duty_pct=0, fan2_duty_pct=0))()
    monkeypatch.setattr(service, "read_telemetry", lambda ec: hot)
    for _ in range(8):  # smoothing takes a few ticks to arrive
        d._tick()
    assert d.kbd_driver.shown.rgb[0] > 200 and d.kbd_driver.shown.rgb[2] < 50


def test_notify_flash_setting_round_trips(daemon):
    d = daemon
    _call(d, "SetKeyboardNotifyFlash", "(b)", True)
    assert state.load(d.state_path)["keyboard"]["notify_flash"] is True


def test_flash_blinks_and_is_not_saved(daemon):
    d = daemon
    _call(d, "SetKeyboardColor", "(uuu)", 0, 0, 255)
    saved = state.load(d.state_path)
    d.ec.writes.clear()
    assert _call(d, "FlashKeyboard").returned
    assert d.kbd_driver.shown.rgb == lt.FLASH_RGB
    assert state.load(d.state_path) == saved


def test_flash_does_nothing_while_off(daemon):
    d = daemon
    _call(d, "SetKeyboardEnabled", "(b)", False)
    d.ec.writes.clear()
    assert _call(d, "FlashKeyboard").returned
    assert d.ec.writes == []


def test_refused_write_rolls_back_the_change(daemon):
    d = daemon
    _call(d, "SetKeyboardColor", "(uuu)", 0, 0, 255)
    d.ec.write_error = OSError("EC busy")
    inv = _call(d, "SetKeyboardColor", "(uuu)", 255, 0, 0)
    assert inv.error and inv.error[0].endswith("Failed")
    assert d.keyboard.rgb == (0, 0, 255)  # never claims the red it could not show
    assert state.load(d.state_path)["keyboard"]["r"] == 0


def test_first_ever_set_refused_leaves_no_keyboard(daemon):
    d = daemon
    d.ec.write_error = OSError("EC busy")
    _call(d, "SetKeyboardColor", "(uuu)", 255, 0, 0)
    assert d.keyboard is None  # fan-only users stay untouched


def test_old_state_file_restores_as_static(make_daemon, tmp_path):
    p = tmp_path / "state.json"
    state.save(str(p), {"profile": "firmware", "keyboard":
                        {"enabled": True, "r": 1, "g": 2, "b": 3, "brightness": 40}})
    d = make_daemon()
    d.state_path = str(p)
    d._restore_saved_state()
    assert d.keyboard.effect == kb.STATIC and d.keyboard.idle_timeout_s == 0
    assert d.kbd_driver.shown == lt.Look(True, (1, 2, 3), mailbox.pct_to_byte(40))


def test_corrupt_lighting_settings_fall_back(make_daemon, tmp_path):
    k = kb.KeyboardState.from_dict({"effect": "disco", "idle_timeout": 1, "period": "x"})
    assert k.effect == kb.STATIC and k.idle_timeout_s == 0


def test_resume_restarts_the_idle_clock(daemon):
    d = daemon
    _call(d, "SetKeyboardIdleTimeout", "(u)", 10)
    d.clock.mono += 100
    d._on_light_timer()
    while d._light_source:
        d.clock.mono += 1
        _fire_timer(d)
    assert d._kbd_dimmed
    d._apply_keyboard("resume")
    assert not d._kbd_dimmed and d.kbd_driver.shown.on


def test_shutdown_leaves_the_plain_colour(daemon):
    d = daemon
    _call(d, "SetKeyboardColor", "(uuu)", 9, 9, 9)
    _call(d, "SetKeyboardIdleTimeout", "(u)", 10)
    d.ec.writes.clear()
    d._leave_keyboard_steady()
    assert (mailbox.FBUF, kb.ENABLE_ON) in d.ec.writes
    assert not d.activity.running


def test_going_to_sleep_pauses_effects(daemon):
    d = daemon
    _call(d, "SetKeyboardEffect", "(su)", "breathe", 0)
    assert d._light_source
    d._on_prepare_for_sleep(None, None, None, None, None, GLib.Variant("(b)", (True,)))
    assert d._light_source == 0
    d._on_prepare_for_sleep(None, None, None, None, None, GLib.Variant("(b)", (False,)))
    assert d._light_source  # resume picks the breathing back up
