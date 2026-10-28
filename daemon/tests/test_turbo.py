"""Turbo Boost knob: sysfs reads/writes against a temp file, plus the
daemon surface (property, authorized method, persistence choice)."""
import os

import pytest

from gigactld import turbo as turbo_mod


def _knob(tmp_path, value="1"):
    p = tmp_path / "no_turbo"
    p.write_text(value)
    return turbo_mod.TurboControl(str(p))


def test_enabled_reflects_no_turbo(tmp_path):
    assert _knob(tmp_path, "1").enabled() is False
    assert _knob(tmp_path, "0").enabled() is True
    assert _knob(tmp_path, "1").available() is True


def test_set_enabled_writes_the_inverse(tmp_path):
    knob = _knob(tmp_path, "1")
    knob.set_enabled(True)
    assert knob.enabled() is True
    knob.set_enabled(False)
    assert knob.enabled() is False


def test_missing_knob_is_unavailable(tmp_path):
    knob = turbo_mod.TurboControl(str(tmp_path / "absent-dir" / "no_turbo"))
    assert knob.available() is False
    with pytest.raises(turbo_mod.TurboUnavailable):
        knob.enabled()
    with pytest.raises(turbo_mod.TurboUnavailable):
        knob.set_enabled(True)


def test_daemon_property_reports_live_state(make_daemon, tmp_path):
    from gigactld.service import INTERFACE
    d = make_daemon()
    d.turbo = _knob(tmp_path, "1")
    get = lambda p: d._get_property(None, None, None, INTERFACE, p)
    assert get("TurboAvailable").get_boolean() is True
    assert get("TurboBoost").get_boolean() is False


def test_daemon_set_turbo_persists_choice(make_daemon, tmp_path):
    from conftest import Invocation

    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import GLib

    from gigactld.service import INTERFACE
    d = make_daemon()
    knob_path = tmp_path / "no_turbo"
    knob_path.write_text("1")
    d.turbo = turbo_mod.TurboControl(str(knob_path))
    inv = Invocation()
    d._method_call(None, ":1.9", None, INTERFACE, "SetTurboBoost",
                   GLib.Variant("(b)", (True,)), inv)
    assert inv.error is None and inv.returned
    assert knob_path.read_text() == "0"
    import json
    saved = json.loads(open(d.state_path).read())
    assert saved["turbo_enabled"] is True


def test_daemon_restore_applies_saved_turbo(make_daemon, tmp_path):
    import json
    d = make_daemon()
    knob_path = tmp_path / "no_turbo"
    knob_path.write_text("0")
    d.turbo = turbo_mod.TurboControl(str(knob_path))
    open(d.state_path, "w").write(json.dumps(
        {"profile": "firmware", "linked": True, "cpu": [], "gpu": [],
         "turbo_enabled": False}))
    d._restore_saved_state()
    assert knob_path.read_text() == "1"


# --- clients see the live knob, whoever moved it ------------------------------

def _announced(d, prop):
    """The last value of ``prop`` the daemon announced via PropertiesChanged."""
    for _iface, signal, params in reversed(d._conn.emissions):
        if signal == "PropertiesChanged" and prop in params[1]:
            return params[1][prop]
    return None


def test_a_change_made_elsewhere_reaches_clients(make_daemon, tmp_path):
    """power-profiles-daemon writes the same knob — Power Saver turns turbo
    off. Clients read a cached copy of the property, so unless the daemon
    announces the change the switch in the window shows the old state."""
    d = make_daemon()
    d.turbo = _knob(tmp_path, "0")
    d._tick()
    (tmp_path / "no_turbo").write_text("1")  # someone else turned turbo off
    d._tick()
    assert _announced(d, "TurboBoost") is False


def test_a_steady_knob_is_not_announced_every_tick(make_daemon, tmp_path):
    """The tick runs every two seconds. Re-announcing an unchanged value would
    wake every client for nothing."""
    d = make_daemon()
    d.turbo = _knob(tmp_path, "0")
    d._tick()

    def turbo_announcements():
        return sum(1 for _i, s, p in d._conn.emissions
                   if s == "PropertiesChanged" and "TurboBoost" in p[1])

    before = turbo_announcements()
    d._tick()
    d._tick()
    assert turbo_announcements() == before


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write a read-only file")
def test_turbo_locked_by_the_firmware_is_explained_in_words(make_daemon, tmp_path):
    """The kernel refuses to turn turbo on when the BIOS has switched it off.
    The user should read why, not an errno — the lesson of #25. (A read-only
    file stands in for the kernel's refusal: both raise PermissionError.)"""
    from conftest import Invocation

    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import GLib

    from gigactld.service import INTERFACE
    d = make_daemon()
    knob = tmp_path / "no_turbo"
    knob.write_text("1")
    knob.chmod(0o444)
    d.turbo = turbo_mod.TurboControl(str(knob))
    inv = Invocation()
    d._method_call(None, ":1.9", None, INTERFACE, "SetTurboBoost",
                   GLib.Variant("(b)", (True,)), inv)
    assert inv.error is not None
    assert "BIOS" in inv.error[1] and "Errno" not in inv.error[1], inv.error


class _RecordingAuthorizer:
    """Answers polkit with ``allow`` and remembers which actions were asked."""

    def __init__(self, allow: bool) -> None:
        self.allow = allow
        self.asked = []

    def check(self, sender, action):
        self.asked.append(action)
        return self.allow


def test_set_turbo_boost_asks_polkit_for_the_cpu_action_and_obeys_a_no(make_daemon, tmp_path):
    """Turbo has its own polkit action, so an admin can allow fans and
    keyboard without allowing CPU changes. A refusal must write nothing."""
    from conftest import Invocation

    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import GLib

    from gigactld import authz
    from gigactld.service import INTERFACE
    d = make_daemon()
    knob = tmp_path / "no_turbo"
    knob.write_text("1")
    d.turbo = turbo_mod.TurboControl(str(knob))
    d.authorizer = _RecordingAuthorizer(allow=False)
    inv = Invocation()
    d._method_call(None, ":1.9", None, INTERFACE, "SetTurboBoost",
                   GLib.Variant("(b)", (True,)), inv)
    assert d.authorizer.asked == [authz.ACTION_CONTROL_CPU]
    assert inv.error is not None and inv.error[0].endswith("NotAuthorized")
    assert knob.read_text() == "1", "a refused request must not touch the knob"


def test_a_knob_the_user_never_set_is_never_written(make_daemon, tmp_path):
    """Only a choice made through the daemon is re-applied. A saved state with
    no turbo choice — every install before this feature, and every user who
    never touched the switch — must leave the knob to tuned/PPD/the firmware,
    at start and on every tick."""
    import json
    d = make_daemon()
    knob = tmp_path / "no_turbo"
    knob.write_text("1")
    d.turbo = turbo_mod.TurboControl(str(knob))
    open(d.state_path, "w").write(json.dumps(
        {"profile": "firmware", "linked": True, "cpu": [], "gpu": []}))
    before = knob.stat().st_mtime_ns
    d._restore_saved_state()
    for _ in range(3):
        d._tick()
    assert knob.stat().st_mtime_ns == before and knob.read_text() == "1"
