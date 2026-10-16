"""Notice desktop notifications so the keyboard can flash for them.

Notifications live on the user's *session* bus, which the root daemon cannot
see, so this half lives in the app (it already runs in the tray all session)
and asks the daemon to ``FlashKeyboard`` when one arrives.

It listens as a D-Bus *monitor* (``BecomeMonitor``): a passive, read-only copy
of the calls apps make to post notifications. A monitor can never send, reply
or interfere, and the desktop's notification server keeps working exactly as
before. Three ways in are covered, because apps use all three:

* ``org.freedesktop.Notifications.Notify`` — libnotify, browsers, chat apps;
* ``org.gtk.Notifications.AddNotification`` — GApplication apps on GNOME;
* ``org.freedesktop.portal.Notification.AddNotification`` — Flatpak apps.

Only the fact that a notification arrived is used. Its text is never read,
kept or sent anywhere; the hints are consulted solely to skip the "silent"
kinds — volume and brightness pop-ups, low-priority notices, and updates to a
notification already on screen (download progress) — which would otherwise
blink the keys every time the user touched a volume key.
"""
from __future__ import annotations

import time
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

FDO = "org.freedesktop.Notifications"
GTK = "org.gtk.Notifications"
PORTAL = "org.freedesktop.portal.Notification"

RULES = (
    f"type='method_call',interface='{FDO}',member='Notify'",
    f"type='method_call',interface='{GTK}',member='AddNotification'",
    f"type='method_call',interface='{PORTAL}',member='AddNotification'",
)

# One flash per this many seconds, however many notifications arrive — a chat
# burst should blink once, not strobe.
COOLDOWN_S = 3.0

# libnotify's on-screen-display hints (volume/brightness pop-ups).
_OSD_HINTS = ("x-canonical-private-synchronous", "synchronous",
              "x-dunst-stack-tag")


def _hint(hints: dict, key: str):
    v = hints.get(key)
    return v.unpack() if isinstance(v, GLib.Variant) else v


def should_flash(interface: str, member: str, args: tuple,
                 own_app_ids: tuple[str, ...] = ()) -> bool:
    """Whether a notification call deserves a flash. Pure, so every rule is
    unit-tested; ``args`` is the call's unpacked body."""
    try:
        if interface == FDO and member == "Notify":
            app_name, replaces_id, _icon, _summary, _body, _actions, hints, _t = args
            if replaces_id:
                return False  # an update to one already shown
            if app_name in own_app_ids:
                return False
            if _hint(hints, "urgency") == 0:
                return False  # low urgency
            if _hint(hints, "transient") or any(k in hints for k in _OSD_HINTS):
                return False
            return True
        if interface == GTK and member == "AddNotification":
            app_id, _id, notification = args
            if app_id in own_app_ids:
                return False
            return _hint(notification, "priority") != "low"
        if interface == PORTAL and member == "AddNotification":
            _id, notification = args
            return _hint(notification, "priority") != "low"
    except (ValueError, TypeError, AttributeError):
        return False  # a malformed call is not worth a flash
    return False


class NotificationWatcher:
    """Calls ``on_notification()`` (on the main loop) for each notification
    worth a flash, at most once per ``COOLDOWN_S``.

    ``address`` exists for tests, which point it at a private bus."""

    def __init__(self, on_notification: Callable[[], None],
                 own_app_ids: tuple[str, ...] = (),
                 address: str | None = None) -> None:
        self._on_notification = on_notification
        self._own = own_app_ids
        self._address = address
        self._conn: Gio.DBusConnection | None = None
        self._cancel: Gio.Cancellable | None = None
        self._last = 0.0
        self.monitoring = False

    @property
    def running(self) -> bool:
        return self._cancel is not None

    def start(self) -> None:
        if self.running:
            return
        self._cancel = Gio.Cancellable()
        try:
            address = self._address or Gio.dbus_address_get_for_bus_sync(
                Gio.BusType.SESSION, None)
        except GLib.Error as exc:
            print(f"gigactl-gui: no session bus to watch notifications on: {exc}",
                  flush=True)
            self._cancel = None
            return
        # A monitor connection must be its own: once it becomes a monitor it
        # can do nothing else, so it cannot share the app's session connection.
        Gio.DBusConnection.new_for_address(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, self._cancel, self._on_connected, self._cancel)

    def stop(self) -> None:
        if self._cancel is not None:
            self._cancel.cancel()
            self._cancel = None
        if self._conn is not None:
            self._conn.close(None, None, None)
            self._conn = None
        self.monitoring = False

    def _on_connected(self, _source, result, cancel) -> None:
        try:
            conn = Gio.DBusConnection.new_for_address_finish(result)
        except GLib.Error as exc:
            if cancel is self._cancel:  # not a stop() we asked for
                print(f"gigactl-gui: cannot watch notifications: {exc}", flush=True)
                self._cancel = None
            return
        if cancel is not self._cancel:  # stopped (or restarted) meanwhile
            conn.close(None, None, None)
            return
        self._conn = conn
        conn.add_filter(self._filter)
        conn.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                  "org.freedesktop.DBus.Monitoring", "BecomeMonitor",
                  GLib.Variant("(asu)", (list(RULES), 0)), None,
                  Gio.DBusCallFlags.NONE, -1, self._cancel,
                  self._on_monitoring, cancel)

    def _on_monitoring(self, conn, result, cancel) -> None:
        try:
            conn.call_finish(result)
        except GLib.Error as exc:
            if cancel is self._cancel:
                print(f"gigactl-gui: the session bus refused to let us watch "
                      f"notifications: {exc}", flush=True)
            return
        self.monitoring = True

    def _filter(self, _conn, message: Gio.DBusMessage, incoming: bool):
        """Runs on GDBus's worker thread, so it only decides and hands off to
        the main loop.

        Monitored method calls are dropped here (None): left in, GDBus would
        try to *answer* a call it was only shown, and a monitor that sends
        anything is disconnected by the bus. Everything else — not least the
        reply to our own ``BecomeMonitor`` — passes through untouched."""
        if not incoming or message.get_message_type() != Gio.DBusMessageType.METHOD_CALL:
            return message
        try:
            body = message.get_body()
            args = body.unpack() if body is not None else ()
            if should_flash(message.get_interface() or "",
                            message.get_member() or "", args, self._own):
                GLib.idle_add(self._fire)
        except Exception as exc:  # never let it escape into the worker thread
            print(f"gigactl-gui: notification filter failed: {exc}", flush=True)
        return None

    def _fire(self) -> bool:
        now = time.monotonic()
        if now - self._last >= COOLDOWN_S:
            self._last = now
            try:
                self._on_notification()
            except Exception as exc:
                print(f"gigactl-gui: notification handler failed: {exc}", flush=True)
        return GLib.SOURCE_REMOVE
