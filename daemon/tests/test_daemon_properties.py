"""The D-Bus property surface: what the GUI reads back to show current state,
and the change notifications that keep it in sync. No bus, no hardware."""
from contextlib import nullcontext

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from gigactld import keyboard as kb  # noqa: E402
from gigactld.service import INTERFACE, Daemon  # noqa: E402


class RecordingEc:
    backend = type("B", (), {"name": "fake"})()

    def __init__(self):
        self.writes = []

    def write_u8(self, off, val):
        self.writes.append((off, val))

    def read_u8(self, off):
        return 0

    def read_u16_be(self, hi, lo):
        return 0  # tach period 0 -> 0 rpm; enough for the apply-now path

    def transaction(self):
        return nullcontext()


class DummyAuth:
    def check(self, sender, action):
        return True


class RecordingConn:
    """Captures emit_signal so PropertiesChanged can be asserted."""

    def __init__(self):
        self.emissions = []  # (iface, signal, unpacked params)

    def emit_signal(self, dest, path, iface, signal, params):
        self.emissions.append((iface, signal, params.unpack()))


class Invocation:
    def __init__(self):
        self.returned = False
        self.error = None

    def return_value(self, _v):
        self.returned = True

    def return_dbus_error(self, name, msg):
        self.error = (name, msg)


def _daemon():
    d = Daemon(RecordingEc(), authorizer=DummyAuth())
    d._conn = RecordingConn()
    return d


def _get(d, prop):
    return d._get_property(None, None, None, INTERFACE, prop)


def test_active_profile_property_reflects_engine():
    d = _daemon()
    d.engine.set_profile("performance")
    assert _get(d, "ActiveProfile").get_string() == "performance"


def test_keyboard_state_property_reports_remembered_state():
    d = _daemon()
    d.keyboard = kb.KeyboardState(enabled=True, r=255, g=128, b=0, brightness_pct=60)
    assert _get(d, "KeyboardState").unpack() == (True, 255, 128, 0, 60)


def test_keyboard_state_property_falls_back_to_firmware_default():
    d = _daemon()  # nothing set yet (lazy None)
    assert d.keyboard is None
    # honest fallback: the firmware default the EC itself boots with
    assert _get(d, "KeyboardState").unpack() == (True, 0, 0, 255, 100)


def test_unknown_property_is_none():
    assert _get(_daemon(), "NoSuchProperty") is None


def test_set_profile_notifies_active_profile_changed():
    d = _daemon()
    d._set_profile(None, GLib.Variant("(s)", ("quiet",)), Invocation())
    changed = [e for e in d._conn.emissions if e[1] == "PropertiesChanged"]
    assert changed, "expected a PropertiesChanged emission"
    iface, props, _invalidated = changed[-1][2]
    assert iface == INTERFACE
    assert props["ActiveProfile"] == "quiet"


def test_set_keyboard_color_notifies_keyboard_state_changed():
    d = _daemon()
    d._set_keyboard_color(None, GLib.Variant("(uuu)", (255, 0, 0)), Invocation())
    changed = [e for e in d._conn.emissions if e[1] == "PropertiesChanged"]
    assert changed
    _iface, props, _inv = changed[-1][2]
    assert props["KeyboardState"] == (True, 255, 0, 0, 100)


def test_restore_firmware_notifies_profile_changed():
    d = _daemon()
    d.engine.set_profile("max")
    d._restore_firmware(None, Invocation())
    _iface, props, _inv = [e for e in d._conn.emissions
                           if e[1] == "PropertiesChanged"][-1][2]
    assert props["ActiveProfile"] == "firmware"


def test_notify_without_connection_is_safe():
    d = Daemon(RecordingEc(), authorizer=DummyAuth())  # _conn stays None
    d._notify_properties(ActiveProfile=GLib.Variant("s", "quiet"))  # must not raise
