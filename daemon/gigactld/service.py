"""D-Bus system-bus service.

The daemon owns its well-known name, broadcasts a ``Telemetry`` signal every
poll interval, runs the automatic fan-curve engine on that same tick, and
exposes fan-control methods (all gated by polkit).
"""
from __future__ import annotations

import signal
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import __version__  # noqa: E402
from . import authz, curve, fans, keyboard, state  # noqa: E402
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
    <method name="SetProfile">
      <arg name="name" type="s" direction="in"/>
    </method>
    <method name="SetCurve">
      <arg name="which"  type="s"     direction="in"/>
      <arg name="points" type="a(uu)" direction="in"/>
      <arg name="linked" type="b"     direction="in"/>
    </method>
    <method name="SetKeyboardColor">
      <arg name="r" type="u" direction="in"/>
      <arg name="g" type="u" direction="in"/>
      <arg name="b" type="u" direction="in"/>
    </method>
    <method name="SetKeyboardBrightness">
      <arg name="percent" type="u" direction="in"/>
    </method>
    <method name="SetKeyboardEnabled">
      <arg name="on" type="b" direction="in"/>
    </method>
    <signal name="Telemetry">
      <arg name="cpu_temp"  type="u"/>
      <arg name="gpu_temp"  type="u"/>
      <arg name="fan1_rpm"      type="u"/>
      <arg name="fan2_rpm"      type="u"/>
      <arg name="fan1_duty_pct" type="u"/>
      <arg name="fan2_duty_pct" type="u"/>
    </signal>
    <property name="DaemonVersion" type="s" access="read"/>
    <property name="ActiveProfile" type="s" access="read"/>
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
        self.engine = curve.ProfileEngine(hysteresis=curve.HYSTERESIS_C)
        self.state_path = state.DEFAULT_PATH

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
        # Clean-stop failsafe: hand the fans back to firmware so a stop never
        # leaves them frozen. (Crash is covered by `gigactld --restore-firmware`
        # in the unit's ExecStopPost.)
        print("gigactld: shutting down; handing fans back to firmware", flush=True)
        try:
            for f in fans.FANS:
                fans.restore_auto(self.ec, f)
        except Exception as exc:
            print(f"gigactld: firmware restore on shutdown failed: {exc}", flush=True)
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
        self._restore_saved_profile()
        GLib.timeout_add_seconds(self.interval_s, self._tick)
        self._tick()  # emit + apply immediately, don't wait a full interval

    def _restore_saved_profile(self) -> None:
        data = state.load(self.state_path)
        if not data:
            return
        try:
            state.restore(self.engine, data)
            print(f"gigactld: restored profile '{self.engine.profile}'", flush=True)
        except Exception as exc:
            print(f"gigactld: could not restore saved profile: {exc}", flush=True)

    def _on_name_acquired(self, conn, name: str) -> None:
        print(f"gigactld: owning {name} (EC backend: {self.ec.backend.name})", flush=True)

    def _on_name_lost(self, conn, name: str) -> None:
        print(f"gigactld: lost name {name}; is another instance running?", flush=True)
        self._loop.quit()

    # --- D-Bus surface -------------------------------------------------------
    def _get_property(self, conn, sender, path, iface, prop):
        if prop == "DaemonVersion":
            return GLib.Variant("s", __version__)
        if prop == "ActiveProfile":
            return GLib.Variant("s", self.engine.profile)
        return None

    def _method_call(self, conn, sender, path, iface, method, params, invocation):
        try:
            if method == "SetFanDuty":
                self._set_fan_duty(sender, params, invocation)
            elif method == "RestoreFirmware":
                self._restore_firmware(sender, invocation)
            elif method == "SetProfile":
                self._set_profile(sender, params, invocation)
            elif method == "SetCurve":
                self._set_curve(sender, params, invocation)
            elif method == "SetKeyboardColor":
                self._set_keyboard_color(sender, params, invocation)
            elif method == "SetKeyboardBrightness":
                self._set_keyboard_brightness(sender, params, invocation)
            elif method == "SetKeyboardEnabled":
                self._set_keyboard_enabled(sender, params, invocation)
            else:
                invocation.return_dbus_error(f"{_ERR}.UnknownMethod", method)
        except Exception as exc:  # never let an exception escape into GLib
            invocation.return_dbus_error(f"{_ERR}.Failed", str(exc))

    def _authorized(self, sender, invocation,
                    action: str = authz.ACTION_CONTROL_FANS,
                    what: str = "the fans") -> bool:
        if self.authorizer.check(sender, action):
            return True
        invocation.return_dbus_error(
            f"{_ERR}.NotAuthorized", f"not authorized to control {what}")
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
        # manual is a transient override — stop the engine fighting it, but do
        # NOT persist it; the last real profile stays saved and is restored on
        # the next start/reboot.
        self.engine.set_manual()
        invocation.return_value(None)

    def _restore_firmware(self, sender, invocation) -> None:
        if not self._authorized(sender, invocation):
            return
        self.engine.set_profile(curve.FIRMWARE)
        for f in fans.FANS:
            fans.restore_auto(self.ec, f)
        self._persist()
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
            self._run_engine(t.cpu_temp, t.gpu_temp)
        except Exception as exc:
            print(f"gigactld: tick failed: {exc}", flush=True)
        return True  # keep the timeout running

    def _run_engine(self, cpu_temp: int, gpu_temp: int) -> None:
        for fan in fans.FANS:
            src = cpu_temp if fan == fans.CPU_FAN else gpu_temp
            duty = self.engine.decide(fan, src, cpu_temp)
            if duty is not None:
                fans.set_duty(self.ec, fan, duty)

    def _apply_now(self) -> None:
        t = read_telemetry(self.ec)
        self._run_engine(t.cpu_temp, t.gpu_temp)

    def _persist(self) -> None:
        try:
            state.save(self.state_path, state.snapshot(self.engine))
        except Exception as exc:
            print(f"gigactld: could not persist state: {exc}", flush=True)

    # --- profile / curve methods --------------------------------------------
    def _set_profile(self, sender, params, invocation) -> None:
        (name,) = params.unpack()
        if not self._authorized(sender, invocation):
            return
        try:
            self.engine.set_profile(name)
        except ValueError as exc:
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs", str(exc))
            return
        if name == curve.FIRMWARE:
            for f in fans.FANS:
                fans.restore_auto(self.ec, f)
        else:
            self._apply_now()  # take effect immediately, not on the next tick
        self._persist()
        invocation.return_value(None)

    def _set_curve(self, sender, params, invocation) -> None:
        which, points, linked = params.unpack()
        if not self._authorized(sender, invocation):
            return
        try:
            self.engine.set_one_curve(which, points, linked)
        except ValueError as exc:
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs", str(exc))
            return
        self._apply_now()
        self._persist()
        invocation.return_value(None)

    # --- keyboard methods ----------------------------------------------------
    def _kbd_authorized(self, sender, invocation) -> bool:
        return self._authorized(sender, invocation,
                                authz.ACTION_CONTROL_KEYBOARD, "the keyboard")

    def _set_keyboard_color(self, sender, params, invocation) -> None:
        r, g, b = params.unpack()
        if not self._kbd_authorized(sender, invocation):
            return
        keyboard.set_color(self.ec, r, g, b)
        invocation.return_value(None)

    def _set_keyboard_brightness(self, sender, params, invocation) -> None:
        (percent,) = params.unpack()
        if not 0 <= percent <= 100:
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs", f"bad percent {percent}")
            return
        if not self._kbd_authorized(sender, invocation):
            return
        keyboard.set_brightness(self.ec, percent)
        invocation.return_value(None)

    def _set_keyboard_enabled(self, sender, params, invocation) -> None:
        (on,) = params.unpack()
        if not self._kbd_authorized(sender, invocation):
            return
        keyboard.set_enabled(self.ec, on)
        invocation.return_value(None)

    def stop(self) -> None:
        self._loop.quit()
