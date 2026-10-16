"""The pure parts of the D-Bus client: telemetry/keyboard parsing, and what a
control call does when the daemon is not there."""
import pytest

from gigactl_gui.client import CurveSnapshot, DaemonClient, KeyboardSnapshot, Telemetry


# --- telemetry ---------------------------------------------------------------

def test_from_tuple_maps_all_fields():
    tm = Telemetry.from_tuple((91, 73, 2486, 2411, 27, 30))
    assert (tm.cpu_temp, tm.gpu_temp) == (91, 73)
    assert (tm.fan1_rpm, tm.fan2_rpm) == (2486, 2411)
    assert (tm.fan1_duty_pct, tm.fan2_duty_pct) == (27, 30)


def test_from_tuple_rejects_wrong_arity():
    with pytest.raises(ValueError):
        Telemetry.from_tuple((1, 2, 3))


# --- keyboard state ----------------------------------------------------------

def test_keyboard_snapshot_maps_the_property_tuple():
    ks = KeyboardSnapshot.from_tuple((True, 255, 128, 0, 60))
    assert ks.enabled is True
    assert ks.rgb == (255, 128, 0)
    assert ks.brightness_pct == 60


def test_keyboard_snapshot_rejects_wrong_arity():
    with pytest.raises(ValueError):
        KeyboardSnapshot.from_tuple((True, 1, 2))


# --- curves -----------------------------------------------------------------

def test_curve_snapshot_maps_the_property_tuple():
    cs = CurveSnapshot.from_tuple((False, [(40, 20), (95, 90)], [(40, 50)]))
    assert cs.linked is False
    assert cs.cpu == [(40, 20), (95, 90)]
    assert cs.gpu == [(40, 50)]


def test_curve_snapshot_tolerates_empty_curves():
    cs = CurveSnapshot.from_tuple((True, [], []))
    assert cs.cpu == [] and cs.gpu == []   # firmware/manual drive nothing


def test_curve_snapshot_rejects_wrong_arity():
    with pytest.raises(ValueError):
        CurveSnapshot.from_tuple((True, []))


# --- control calls with no daemon -------------------------------------------

def _errors_from(action) -> list[str]:
    """Run a control call on a client that was never started (no proxy)."""
    seen: list[str] = []
    client = DaemonClient(on_error=seen.append)
    action(client)
    return seen


@pytest.mark.parametrize("action", [
    lambda c: c.set_profile("quiet"),
    lambda c: c.set_keyboard_color(255, 0, 0),
    lambda c: c.set_keyboard_brightness(50),
    lambda c: c.set_keyboard_enabled(True),
    lambda c: c.set_curve("cpu", [(40, 30)], True),
    lambda c: c.set_keyboard_effect("cycle", 300),
    lambda c: c.set_keyboard_idle_timeout(30),
    lambda c: c.set_keyboard_notify_flash(True),
    lambda c: c.flash_keyboard(),
], ids=["profile", "colour", "brightness", "enabled", "curve",
        "effect", "idle", "notify-flash", "flash"])
def test_control_call_without_daemon_reports_not_running(action):
    seen = _errors_from(action)
    assert seen, "a control call with no daemon must surface an error"
    assert "not running" in seen[0].lower()


# --- EcStatus (#25) ---------------------------------------------------------

class _FakeProxy:
    def __init__(self, props):
        self._props = props

    def get_cached_property(self, name):
        return self._props.get(name)


def _client_with(props):
    c = DaemonClient()
    c._proxy = _FakeProxy(props)
    return c


def test_ec_status_reads_the_property():
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import GLib

    c = _client_with({"EcStatus": GLib.Variant("s", "read-only, run modprobe")})
    assert c.ec_status() == "read-only, run modprobe"


def test_ec_status_is_empty_without_a_daemon():
    assert DaemonClient().ec_status() == ""


def test_ec_status_is_empty_against_a_daemon_too_old_to_have_it():
    """An install can be older than the app talking to it; a missing property
    means nothing to report, not a problem to announce."""
    assert _client_with({}).ec_status() == ""


def test_quiet_flash_without_daemon_says_nothing():
    # a missed notification flash is not worth an error toast
    assert _errors_from(lambda c: c.flash_keyboard(quiet=True)) == []


def test_effects_and_idle_read_the_properties():
    from gi.repository import GLib
    c = _client_with({
        "KeyboardEffects": GLib.Variant("(suub)", ("cycle", 300, 30, True)),
        "KeyboardIdle": GLib.Variant("b", True),
    })
    assert c.keyboard_effects().effect == "cycle"
    assert c.keyboard_idle() is True


def test_older_daemon_has_no_effects():
    c = _client_with({})
    assert c.keyboard_effects() is None  # the window hides those controls
    assert c.keyboard_idle() is False
