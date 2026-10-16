"""Notification flashing: which notifications count (pure rules), and that the
monitor really sees them on a live bus without disturbing the notification
server — run against a private dbus-daemon, skipped where there is none."""
import os
import shutil
import subprocess
import time

import gi
import pytest

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from gigactl_gui import notify_watch as nw  # noqa: E402


def _fdo(app="Firefox", replaces=0, hints=None):
    return (app, replaces, "", "Hi", "body", [], hints or {}, -1)


# --- the rules ------------------------------------------------------------------

def test_an_ordinary_notification_flashes():
    assert nw.should_flash(nw.FDO, "Notify", _fdo())


def test_updates_low_urgency_and_osd_do_not_flash():
    assert not nw.should_flash(nw.FDO, "Notify", _fdo(replaces=7))
    assert not nw.should_flash(nw.FDO, "Notify",
                               _fdo(hints={"urgency": GLib.Variant("y", 0)}))
    assert not nw.should_flash(nw.FDO, "Notify",
                               _fdo(hints={"transient": GLib.Variant("b", True)}))
    assert not nw.should_flash(nw.FDO, "Notify", _fdo(
        hints={"x-canonical-private-synchronous": GLib.Variant("s", "volume")}))


def test_critical_flashes():
    assert nw.should_flash(nw.FDO, "Notify",
                           _fdo(hints={"urgency": GLib.Variant("y", 2)}))


def test_our_own_notifications_do_not_flash():
    own = ("GigaControl", "io.github.smairio.gigactl")
    assert not nw.should_flash(nw.FDO, "Notify", _fdo(app="GigaControl"), own)
    assert not nw.should_flash(nw.GTK, "AddNotification",
                               ("io.github.smairio.gigactl", "x", {}), own)


def test_gtk_and_portal_notifications():
    assert nw.should_flash(nw.GTK, "AddNotification", ("org.gnome.Calendar", "a", {}))
    assert not nw.should_flash(nw.GTK, "AddNotification",
                               ("org.gnome.X", "a", {"priority": GLib.Variant("s", "low")}))
    assert nw.should_flash(nw.PORTAL, "AddNotification", ("a", {}))


def test_malformed_and_unrelated_calls_do_not_flash():
    assert not nw.should_flash(nw.FDO, "Notify", ("too", "short"))
    assert not nw.should_flash(nw.FDO, "CloseNotification", (3,))
    assert not nw.should_flash("org.example", "Notify", _fdo())


# --- live bus -------------------------------------------------------------------

@pytest.fixture
def private_bus():
    exe = shutil.which("dbus-daemon")
    if not exe:
        pytest.skip("no dbus-daemon")
    proc = subprocess.Popen([exe, "--session", "--nofork", "--print-address"],
                            stdout=subprocess.PIPE, text=True)
    address = proc.stdout.readline().strip()
    yield address
    proc.terminate()
    proc.wait(5)


def _pump_until(cond, timeout=5.0):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        ctx.iteration(False)
        time.sleep(0.005)
    return cond()


XML = """<node><interface name="org.freedesktop.Notifications">
<method name="Notify">
 <arg type="s" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/>
 <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="as" direction="in"/>
 <arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="out"/>
</method></interface></node>"""


def _connect(address):
    return Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


def test_monitor_sees_notifications_and_server_still_answers(private_bus, monkeypatch):
    monkeypatch.setattr(nw, "COOLDOWN_S", 0.0)
    # a stand-in notification server
    server = _connect(private_bus)
    received = []

    def on_call(conn, sender, path, iface, method, params, inv):
        received.append(params.unpack()[3])
        inv.return_value(GLib.Variant("(u)", (len(received),)))

    server.register_object("/org/freedesktop/Notifications",
                           Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0],
                           on_call, None, None)
    server.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                     "org.freedesktop.DBus", "RequestName",
                     GLib.Variant("(su)", (nw.FDO, 0)), None,
                     Gio.DBusCallFlags.NONE, -1, None)

    flashes = []
    watcher = nw.NotificationWatcher(lambda: flashes.append(1), address=private_bus)
    watcher.start()
    try:
        assert _pump_until(lambda: watcher.monitoring), "never became a monitor"

        app = _connect(private_bus)
        replies = []

        def send(args):
            app.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                     nw.FDO, "Notify", GLib.Variant("(susssasa{sv}i)", args),
                     GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, -1, None,
                     lambda c, r: replies.append(c.call_finish(r).unpack()[0]))

        send(_fdo())
        assert _pump_until(lambda: flashes and replies)
        assert received == ["Hi"]  # the real server still got it, and answered

        send(_fdo(replaces=1))  # an update: no flash
        assert _pump_until(lambda: len(replies) == 2)
        _pump_until(lambda: False, timeout=0.2)
        assert len(flashes) == 1
        assert not watcher._conn.is_closed()  # the bus never kicked us
    finally:
        watcher.stop()
    assert not watcher.running


def test_cooldown_turns_a_burst_into_one_flash():
    flashes = []
    w = nw.NotificationWatcher(lambda: flashes.append(1))
    for _ in range(5):
        w._fire()
    assert flashes == [1]
