"""D-Bus system-bus service.

For this ticket the daemon owns its well-known name and broadcasts a
``Telemetry`` signal every poll interval. Control methods (fan/keyboard) and
properties land in later tickets; the interface is introspectable so they slot
in beside the signal.
"""
from __future__ import annotations

import signal

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import __version__  # noqa: E402
from .telemetry import read_telemetry  # noqa: E402

BUS_NAME = "io.github.smairio.gigactl"
OBJECT_PATH = "/io/github/smairio/gigactl"
INTERFACE = "io.github.smairio.gigactl.Control"

INTROSPECTION_XML = f"""
<node>
  <interface name="{INTERFACE}">
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
    def __init__(self, ec, interval_s: int = 2) -> None:
        self.ec = ec
        self.interval_s = interval_s
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
                None,                # method_call: none yet
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
