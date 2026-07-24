"""D-Bus system-bus service.

For this ticket the daemon owns its well-known name and broadcasts a
``Telemetry`` signal every poll interval. Control methods (fan/keyboard) and
properties land in later tickets; the interface is introspectable so they slot
in beside the signal.
"""
from __future__ import annotations

import signal
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import __version__  # noqa: E402
from . import authz, fans  # noqa: E402
from .telemetry import read_telemetry  # noqa: E402

_ERR = "io.github.smairio.gigactl.Error"

BUS_NAME = "io.github.smairio.gigactl"
OBJECT_PATH = "/io/github/smairio/gigactl"
INTERFACE = "io.github.smairio.gigactl.Control"

INTROSPECTION_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="SetFanDuty">
      <arg name="fan"     type="u" direction="in"/>
      <arg name="percent" type="u" direction="in"/>
    </method>
    <method name="RestoreFirmware"/>
    <signal name="Telemetry">
      <arg name="cpu_temp"  type="u"/>
      <arg name="gpu_temp"  type="u"/>
      <arg name="fan1_rpm"      type="u"/>
      <arg name="fan2_rpm"      type="u"/>
      <arg name="fan1_duty_pct" type="u"/>
      <arg name="fan2_duty_pct" type="u"/>
    </signal>
    <property name="DaemonVersion" type="s" access="read"/>
  </interface>
</node>
"""


class Daemon:
    def __init__(self, ec, interval_s: int = 2,
                 authorizer: authz.Authorizer | None = None) -> None:
        self.ec = ec
        self.interval_s = interval_s
        self.authorizer = authorizer or authz.Authorizer()
        self._conn: Gio.DBusConnection | None = None
        self._node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        self._loop = GLib.MainLoop()

    # --- lifecycle -----------------------------------------------------------
    def run(self) -> None:
        Gio.bus_own_name(
            Gio.BusType.SYSTEM,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            self._on_name_acquired,
            self._on_name_lost,
        )
        # GLib-level signal handling so systemd's SIGTERM (and Ctrl-C) quit the
        # loop promptly — Python's own signal handlers can stall under GLib.
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._on_signal)
        self._loop.run()

    def _on_signal(self) -> bool:
        print("gigactld: shutting down", flush=True)
        self._loop.quit()
        return GLib.SOURCE_REMOVE

    def _on_bus_acquired(self, conn: Gio.DBusConnection, name: str) -> None:
        self._conn = conn
        # introspection/property registration is best-effort: even if a
        # PyGObject quirk rejects it, the Telemetry broadcast below still works.
        try:
            conn.register_object(
                OBJECT_PATH,
                self._node.interfaces[0],
                self._method_call,   # method_call
                self._get_property,  # get_property
                None,                # set_property
            )
        except Exception as exc:  # pragma: no cover - environment dependent
            print(f"gigactld: object registration skipped ({exc})", flush=True)
        GLib.timeout_add_seconds(self.interval_s, self._tick)
        self._tick()  # emit immediately, don't wait a full interval

    def _on_name_acquired(self, conn, name: str) -> None:
        print(f"gigactld: owning {name} (EC backend: {self.ec.backend.name})", flush=True)

    def _on_name_lost(self, conn, name: str) -> None:
        print(f"gigactld: lost name {name}; is another instance running?", flush=True)
        self._loop.quit()

    # --- D-Bus surface -------------------------------------------------------
    def _get_property(self, conn, sender, path, iface, prop):
        if prop == "DaemonVersion":
            return GLib.Variant("s", __version__)
        return None

    def _method_call(self, conn, sender, path, iface, method, params, invocation):
        try:
            if method == "SetFanDuty":
                self._set_fan_duty(sender, params, invocation)
            elif method == "RestoreFirmware":
                self._restore_firmware(sender, invocation)
            else:
                invocation.return_dbus_error(f"{_ERR}.UnknownMethod", method)
        except Exception as exc:  # never let an exception escape into GLib
            invocation.return_dbus_error(f"{_ERR}.Failed", str(exc))

    def _authorized(self, sender, invocation) -> bool:
        if self.authorizer.check(sender, authz.ACTION_CONTROL_FANS):
            return True
        invocation.return_dbus_error(
            f"{_ERR}.NotAuthorized", "not authorized to control the fans")
        return False

    def _set_fan_duty(self, sender, params, invocation) -> None:
        fan, percent = params.unpack()
        try:
            targets = fans.expand(fan)
        except ValueError as exc:
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs", str(exc))
            return
        if not 0 <= percent <= 100:
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs", f"bad percent {percent}")
            return
        if not self._authorized(sender, invocation):
            return

        target_raw = fans.pct_to_raw(percent)
        for f in targets:
            fans.set_duty(self.ec, f, percent)
        rejected = self._verify(targets, target_raw)
        if rejected:
            # any rejection reverts EVERY fan we touched, not just the bad one,
            # so a partial failure can't strand a sibling in manual mode.
            for f in targets:
                fans.restore_auto(self.ec, f)
            invocation.return_dbus_error(
                f"{_ERR}.WriteRejected",
                f"fan(s) {rejected} did not accept the duty; all reverted to firmware auto")
            return
        invocation.return_value(None)

    def _restore_firmware(self, sender, invocation) -> None:
        if not self._authorized(sender, invocation):
            return
        for f in fans.FANS:
            fans.restore_auto(self.ec, f)
        invocation.return_value(None)

    def _verify(self, targets, target_raw: int) -> list:
        """Return the fans that never took the write. The duty register snaps
        to the commanded value, so a short settle suffices — no long ramp poll,
        keeping the main loop responsive."""
        rejected = list(targets)
        for _ in range(3):  # ~0.6s total, shared across all fans
            time.sleep(0.2)
            rejected = [f for f in rejected
                        if not fans.duty_matches(target_raw, fans.read_duty(self.ec, f))]
            if not rejected:
                return []
        return rejected

    # --- telemetry loop ------------------------------------------------------
    def _tick(self) -> bool:
        try:
            t = read_telemetry(self.ec)
            self._conn.emit_signal(
                None, OBJECT_PATH, INTERFACE, "Telemetry",
                GLib.Variant(
                    "(uuuuuu)",
                    (t.cpu_temp, t.gpu_temp, t.fan1_rpm, t.fan2_rpm,
                     t.fan1_duty_pct, t.fan2_duty_pct),
                ),
            )
        except Exception as exc:
            print(f"gigactld: telemetry read failed: {exc}", flush=True)
        return True  # keep the timeout running

    def stop(self) -> None:
        self._loop.quit()
