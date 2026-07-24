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
from . import authz, curve, fans, keyboard, mailbox, state  # noqa: E402
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
    <!-- (enabled, r, g, b, brightness_pct) — what the GUI shows as selected -->
    <property name="KeyboardState" type="(buuuu)" access="read"/>
  </interface>
</node>
"""


def _keyboard_variant(kbd: keyboard.KeyboardState | None) -> GLib.Variant:
    """Wire shape for the ``KeyboardState`` property. The D-Bus adapter lives
    here rather than on ``KeyboardState`` so the hardware module stays free of
    GLib; when nothing has been set yet we report the firmware default the EC
    itself boots with, which is honest rather than a guess."""
    k = kbd or keyboard.KeyboardState()
    return GLib.Variant("(buuuu)", (k.enabled, k.r, k.g, k.b, k.brightness_pct))


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
        # Lazily created on first keyboard set (see _remember_keyboard): while
        # it's None the daemon writes no keyboard section and re-applies nothing,
        # so a fan-only user's backlight is never touched.
        self.keyboard: keyboard.KeyboardState | None = None
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
        self._restore_saved_state()
        self._subscribe_sleep(conn)
        GLib.timeout_add_seconds(self.interval_s, self._tick)
        self._tick()  # emit + apply immediately, don't wait a full interval

    def _restore_saved_state(self) -> None:
        data = state.load(self.state_path)
        if not data:
            return
        try:
            state.restore(self.engine, data)
            print(f"gigactld: restored profile '{self.engine.profile}'", flush=True)
        except Exception as exc:
            print(f"gigactld: could not restore saved profile: {exc}", flush=True)
        # Keyboard is optional: only re-apply if we actually saved one, so a
        # fresh install doesn't force the firmware default onto the backlight.
        kd = data.get("keyboard")
        if kd:
            self.keyboard = keyboard.KeyboardState.from_dict(kd)
            self._apply_keyboard("boot")

    def _apply_keyboard(self, why: str) -> None:
        """Re-drive the backlight to the remembered state, if the user has set
        one. Best-effort and self-contained — it runs from a signal callback, so
        an exception must not escape into the GLib loop."""
        if self.keyboard is None:
            return
        try:
            self.keyboard.apply(self.ec)
            print(f"gigactld: keyboard re-applied ({why})", flush=True)
        except Exception as exc:
            print(f"gigactld: keyboard apply failed ({why}): {exc}", flush=True)

    def _subscribe_sleep(self, conn: Gio.DBusConnection) -> None:
        """Listen for logind's PrepareForSleep so we can re-apply the backlight
        on resume (the EC drops it across suspend). Receiving a broadcast signal
        needs no extra D-Bus policy."""
        try:
            conn.signal_subscribe(
                "org.freedesktop.login1",           # sender
                "org.freedesktop.login1.Manager",   # interface
                "PrepareForSleep",                  # signal
                "/org/freedesktop/login1",          # path
                None,                               # arg0 filter
                Gio.DBusSignalFlags.NONE,
                self._on_prepare_for_sleep,
            )
        except Exception as exc:  # pragma: no cover - environment dependent
            print(f"gigactld: could not subscribe to logind sleep signal ({exc})", flush=True)

    def _on_prepare_for_sleep(self, conn, sender, path, iface, signal_name, params):
        # PrepareForSleep(true) fires before sleep, (false) after resume; the
        # backlight only needs re-driving on the resume edge.
        (going_to_sleep,) = params.unpack()
        if not going_to_sleep:
            self._apply_keyboard("resume")

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
        if prop == "KeyboardState":
            return _keyboard_variant(self.keyboard)
        return None

    def _notify_properties(self, **changed) -> None:
        """Emit ``PropertiesChanged`` so a running GUI or tray reflects state it
        did not set itself — another client, a boot restore, or a manual duty
        override flipping the profile. Best-effort: a failed emit must never
        break the call that triggered it."""
        if self._conn is None:
            return
        try:
            self._conn.emit_signal(
                None, OBJECT_PATH,
                "org.freedesktop.DBus.Properties", "PropertiesChanged",
                GLib.Variant("(sa{sv}as)", (INTERFACE, changed, [])),
            )
        except Exception as exc:
            print(f"gigactld: could not emit PropertiesChanged: {exc}", flush=True)

    def _notify_profile(self) -> None:
        self._notify_properties(ActiveProfile=GLib.Variant("s", self.engine.profile))

    def _notify_keyboard(self) -> None:
        self._notify_properties(KeyboardState=_keyboard_variant(self.keyboard))

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

        target_raw = mailbox.pct_to_byte(percent)
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
        self._notify_profile()
        invocation.return_value(None)

    def _restore_firmware(self, sender, invocation) -> None:
        if not self._authorized(sender, invocation):
            return
        self.engine.set_profile(curve.FIRMWARE)
        for f in fans.FANS:
            fans.restore_auto(self.ec, f)
        self._persist()
        self._notify_profile()
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
            state.save(self.state_path, state.snapshot(self.engine, self.keyboard))
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
        self._notify_profile()
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
        self._notify_profile()
        invocation.return_value(None)

    # --- keyboard methods ----------------------------------------------------
    def _kbd_authorized(self, sender, invocation) -> bool:
        return self._authorized(sender, invocation,
                                authz.ACTION_CONTROL_KEYBOARD, "the keyboard")

    def _remember_keyboard(self) -> keyboard.KeyboardState:
        """Keyboard state is created lazily on first use, so a user who only
        ever touches the fans never gets a keyboard section written — and thus
        never has the firmware default re-applied at boot."""
        if self.keyboard is None:
            self.keyboard = keyboard.KeyboardState()
        return self.keyboard

    def _set_keyboard_color(self, sender, params, invocation) -> None:
        r, g, b = params.unpack()
        if not all(0 <= c <= 255 for c in (r, g, b)):
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs",
                                         f"colour components must be 0-255: {(r, g, b)}")
            return
        if not self._kbd_authorized(sender, invocation):
            return
        keyboard.set_color(self.ec, r, g, b)
        self._remember_keyboard().record_colour(r, g, b)
        self._persist()
        self._notify_keyboard()
        invocation.return_value(None)

    def _set_keyboard_brightness(self, sender, params, invocation) -> None:
        (percent,) = params.unpack()
        if not 0 <= percent <= 100:
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs", f"bad percent {percent}")
            return
        if not self._kbd_authorized(sender, invocation):
            return
        keyboard.set_brightness(self.ec, percent)
        self._remember_keyboard().record_brightness(percent)
        self._persist()
        self._notify_keyboard()
        invocation.return_value(None)

    def _set_keyboard_enabled(self, sender, params, invocation) -> None:
        (on,) = params.unpack()
        if not self._kbd_authorized(sender, invocation):
            return
        keyboard.set_enabled(self.ec, on)
        self._remember_keyboard().record_enabled(on)
        self._persist()
        self._notify_keyboard()
        invocation.return_value(None)

    def stop(self) -> None:
        self._loop.quit()
