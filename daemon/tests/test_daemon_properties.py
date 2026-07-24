"""The D-Bus property surface: what the GUI reads back to show current state,
and the change notifications that keep it in sync. No bus, no hardware.

Fakes come from ``daemon/conftest.py``.
"""
from conftest import Invocation

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from gigactld import keyboard as kb  # noqa: E402
from gigactld.service import INTERFACE  # noqa: E402


def _get(daemon, prop):
    return daemon._get_property(None, None, None, INTERFACE, prop)


def test_active_profile_property_reflects_engine(make_daemon):
    d = make_daemon()
    d.engine.set_profile("performance")
    assert _get(d, "ActiveProfile").get_string() == "performance"


def test_keyboard_state_property_reports_remembered_state(make_daemon):
    d = make_daemon()
    d.keyboard = kb.KeyboardState(enabled=True, r=255, g=128, b=0, brightness_pct=60)
    assert _get(d, "KeyboardState").unpack() == (True, 255, 128, 0, 60)


def test_keyboard_state_property_falls_back_to_firmware_default(make_daemon):
    d = make_daemon()  # nothing set yet (lazy None)
    assert d.keyboard is None
    # honest fallback: the firmware default the EC itself boots with
    assert _get(d, "KeyboardState").unpack() == (True, 0, 0, 255, 100)


def test_unknown_property_is_none(make_daemon):
    assert _get(make_daemon(), "NoSuchProperty") is None


def test_set_profile_notifies_active_profile_changed(make_daemon):
    d = make_daemon()
    d._set_profile(None, GLib.Variant("(s)", ("quiet",)), Invocation())
    iface, props, _invalidated = d._conn.properties_changed()
    assert iface == INTERFACE
    assert props["ActiveProfile"] == "quiet"


def test_set_keyboard_color_notifies_keyboard_state_changed(make_daemon):
    d = make_daemon()
    d._set_keyboard_color(None, GLib.Variant("(uuu)", (255, 0, 0)), Invocation())
    _iface, props, _inv = d._conn.properties_changed()
    assert props["KeyboardState"] == (True, 255, 0, 0, 100)


def test_restore_firmware_notifies_profile_changed(make_daemon):
    d = make_daemon()
    d.engine.set_profile("max")
    d._restore_firmware(None, Invocation())
    _iface, props, _inv = d._conn.properties_changed()
    assert props["ActiveProfile"] == "firmware"


def test_manual_duty_notifies_profile_changed(make_daemon):
    """A direct duty override moves the engine to 'manual'; a running GUI must
    hear about it, since it did not make that change itself."""
    d = make_daemon()
    d._set_fan_duty(None, GLib.Variant("(uu)", (0, 60)), Invocation())
    _iface, props, _inv = d._conn.properties_changed()
    assert props["ActiveProfile"] == "manual"


def test_notify_without_connection_is_safe(make_daemon):
    d = make_daemon(connected=False)  # _conn stays None
    d._notify_properties(ActiveProfile=GLib.Variant("s", "quiet"))  # must not raise
