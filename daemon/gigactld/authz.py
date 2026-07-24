"""polkit authorization, called straight over the system bus (no polkit GIR
dependency — just python3-gi).

The daemon runs as root; it must *not* trust callers blindly. For each
control request it asks polkit whether the calling bus peer is authorized for
the action, and polkit applies the policy in the shipped ``.policy`` file
(``allow_active=yes``, ``allow_inactive=no``, ``allow_any=auth_admin``).
"""
from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

ACTION_CONTROL_FANS = "io.github.smairio.gigactl.control-fans"
ACTION_CONTROL_KEYBOARD = "io.github.smairio.gigactl.control-keyboard"

_PK_NAME = "org.freedesktop.PolicyKit1"
_PK_PATH = "/org/freedesktop/PolicyKit1/Authority"
_PK_IFACE = "org.freedesktop.PolicyKit1.Authority"
_ALLOW_INTERACTION = 1  # let a remote admin authenticate; active users aren't prompted


class Authorizer:
    """Checks caller authorization via polkit. Fails closed."""

    def __init__(self, bus: Gio.DBusConnection | None = None) -> None:
        self._bus = bus

    def _connection(self) -> Gio.DBusConnection:
        if self._bus is None:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        return self._bus

    def check(self, sender: str, action_id: str) -> bool:
        """True iff ``sender`` (a unique bus name) is authorized for ``action_id``."""
        # Build the whole argument tuple in one Variant. The subject is a plain
        # Python tuple ("(sa{sv})"); only the a{sv} *values* are wrapped Variants.
        subject = ("system-bus-name", {"name": GLib.Variant("s", sender)})
        params = GLib.Variant(
            "((sa{sv})sa{ss}us)",
            (subject, action_id, {}, _ALLOW_INTERACTION, ""),
        )
        try:
            reply = self._connection().call_sync(
                _PK_NAME, _PK_PATH, _PK_IFACE, "CheckAuthorization",
                params, GLib.VariantType("((bba{ss}))"),
                Gio.DBusCallFlags.NONE, -1, None,
            )
        except GLib.Error as exc:
            print(f"gigactld: polkit check failed, denying: {exc}", flush=True)
            return False
        (is_authorized, _is_challenge, _details) = reply.unpack()[0]
        return bool(is_authorized)
