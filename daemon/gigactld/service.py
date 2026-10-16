"""D-Bus system-bus service.

The daemon owns its well-known name, broadcasts a ``Telemetry`` signal every
poll interval, runs the automatic fan-curve engine on that same tick, and
exposes fan-control methods (all gated by polkit).
"""
from __future__ import annotations

import dataclasses
import signal
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import __version__  # noqa: E402
from . import activity, authz, curve, ec as ec_mod, fans, keyboard, lighting, mailbox, state  # noqa: E402
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
    <!-- effect: static | cycle | breathe | temperature. period_s is seconds
         per loop for cycle/breathe; 0 picks the default speed. -->
    <method name="SetKeyboardEffect">
      <arg name="effect"   type="s" direction="in"/>
      <arg name="period_s" type="u" direction="in"/>
    </method>
    <!-- Fade the keys to dark after this many seconds with no typing; 0 = never. -->
    <method name="SetKeyboardIdleTimeout">
      <arg name="seconds" type="u" direction="in"/>
    </method>
    <!-- Remembered for the desktop app, which watches the session's
         notifications and calls FlashKeyboard when this is on. -->
    <method name="SetKeyboardNotifyFlash">
      <arg name="on" type="b" direction="in"/>
    </method>
    <!-- A few quick white blinks, then back to normal. Does nothing while the
         backlight is switched off or a flash is already running. -->
    <method name="FlashKeyboard"/>
    <signal name="Telemetry">
      <arg name="cpu_temp"  type="u"/>
      <arg name="gpu_temp"  type="u"/>
      <arg name="fan1_rpm"      type="u"/>
      <arg name="fan2_rpm"      type="u"/>
      <arg name="fan1_duty_pct" type="u"/>
      <arg name="fan2_duty_pct" type="u"/>
    </signal>
    <property name="DaemonVersion" type="s" access="read"/>
    <!-- Empty when the Embedded Controller is doing what it is told. Otherwise
         one plain sentence naming what is wrong and what to do about it, so a
         client can show it without having to attempt a write to find out. A
         machine whose EC refuses writes still reports temperatures perfectly
         (#25), and nothing else on this interface reveals the difference. -->
    <property name="EcStatus" type="s" access="read"/>
    <property name="ActiveProfile" type="s" access="read"/>
    <!-- The same six numbers as the Telemetry signal, read on demand. The signal
         is for anything that stays running; this is for one-shot readers such as
         `gfan status`, which cannot wait out a poll interval. Deliberately never
         announced via PropertiesChanged — a client that wants a stream wants the
         signal, not a property notification every two seconds. -->
    <property name="Telemetry" type="(uuuuuu)" access="read"/>
    <!-- (enabled, r, g, b, brightness_pct) — what the GUI shows as selected -->
    <property name="KeyboardState" type="(buuuu)" access="read"/>
    <!-- (effect, period_s, idle_timeout_s, notify_flash). period_s is the speed
         actually in use (the default when none was chosen), so a client can
         draw the same animation the keys show. -->
    <property name="KeyboardEffects" type="(suub)" access="read"/>
    <!-- True while the keys are dark because nobody has typed for the idle
         timeout. Announced on change only. -->
    <property name="KeyboardIdle" type="b" access="read"/>
    <!-- (linked, cpu_points, gpu_points) — the shape the curve editor loads.
         Empty point lists mean the engine is driving nothing (firmware/manual). -->
    <property name="Curves" type="(ba(uu)a(uu))" access="read"/>
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


def _effects_variant(kbd: keyboard.KeyboardState | None) -> GLib.Variant:
    k = kbd or keyboard.KeyboardState()
    return GLib.Variant("(suub)", (k.effect, k.effective_period_s(),
                                   k.idle_timeout_s, k.notify_flash))


def _curves_variant(engine: curve.ProfileEngine) -> GLib.Variant:
    """Wire shape for the ``Curves`` property: the curves the engine is actually
    driving, so opening the editor on a named profile shows that profile's shape
    rather than an invented one. Firmware/manual report empty lists, because in
    those states the engine drives nothing."""
    cpu = [tuple(p) for p in engine.curve_for(curve.CPU_FAN)]
    gpu = [tuple(p) for p in engine.curve_for(curve.GPU_FAN)]
    return GLib.Variant("(ba(uu)a(uu))", (engine.linked, cpu, gpu))


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
        # The keyboard's moment-to-moment look (effects, idle fade, flashes)
        # and the EC writer that only sends what changed. ``lighting.settings``
        # follows ``self.keyboard`` (see the property below).
        self.lighting = lighting.Lighting()
        # Lazily created on first keyboard set (see _remember_keyboard): while
        # it's None the daemon writes no keyboard section and re-applies nothing,
        # so a fan-only user's backlight is never touched.
        self.keyboard = None
        self.kbd_driver = lighting.Driver(ec)
        self._light_source = 0
        self._kbd_fault: str | None = None
        self._kbd_dimmed = False
        self.activity = activity.ActivityWatcher(self._on_key_activity)
        self.state_path = state.DEFAULT_PATH
        # Why the engine's last attempt to drive the fans came to nothing, and
        # the status last announced — kept so a condition repeating every two
        # seconds is reported once rather than every tick.
        self._engine_fault: str | None = None
        self._last_ec_status = ""

    @property
    def keyboard(self) -> keyboard.KeyboardState | None:
        return self.lighting.settings

    @keyboard.setter
    def keyboard(self, kbd: keyboard.KeyboardState | None) -> None:
        # One object, two readers: the lighting engine sees a setting the
        # moment D-Bus changes it, with nothing to keep in sync.
        self.lighting.settings = kbd

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
        self._leave_keyboard_steady()
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
        an exception must not escape into the GLib loop.

        The EC's state is unknown after boot or resume, so the driver forgets
        what it last wrote and sends everything; and the idle clock restarts,
        because opening the lid is as good as a key press."""
        if self.keyboard is None:
            return
        self.kbd_driver.invalidate()
        self.lighting.note_activity(time.monotonic())
        try:
            self._render_keyboard()
            print(f"gigactld: keyboard re-applied ({why})", flush=True)
        except Exception as exc:
            print(f"gigactld: keyboard apply failed ({why}): {exc}", flush=True)
        self._sync_activity()

    # --- keyboard lighting loop ----------------------------------------------
    def _render_keyboard(self) -> None:
        """Show what the backlight should show now, then sleep until that next
        changes. Raises on an EC failure (the caller decides whether a user is
        waiting on it); the reschedule happens either way, so one failed frame
        never freezes an effect."""
        if self._light_source:
            GLib.source_remove(self._light_source)
            self._light_source = 0
        now = time.monotonic()
        try:
            self.kbd_driver.show(self.lighting.look(now, time.time()))
        finally:
            delay = self.lighting.next_delay(time.monotonic())
            if delay is not None:
                self._light_source = GLib.timeout_add(
                    max(10, int(delay * 1000)), self._on_light_timer)
            self._sync_dimmed(now)

    def _render_keyboard_quietly(self) -> None:
        """For timers, key presses and telemetry: a failure is logged once per
        distinct cause rather than every frame, and never escapes."""
        try:
            self._render_keyboard()
            self._kbd_fault = None
        except Exception as exc:
            msg = str(exc)
            if msg != self._kbd_fault:
                print(f"gigactld: keyboard lighting write failed: {msg}", flush=True)
                self._kbd_fault = msg

    def _on_light_timer(self) -> bool:
        self._light_source = 0
        self._render_keyboard_quietly()
        return GLib.SOURCE_REMOVE

    def _on_key_activity(self) -> None:
        # Cheap on purpose: this runs on every key event. Only a press that
        # wakes dimmed keys needs a redraw now; otherwise the pending timer
        # re-reads the (moved) idle deadline when it fires.
        if self.lighting.note_activity(time.monotonic()):
            self._render_keyboard_quietly()

    def _sync_dimmed(self, now: float) -> None:
        dimmed = self.lighting.is_dimmed(now)
        if dimmed != self._kbd_dimmed:
            self._kbd_dimmed = dimmed
            self._notify_properties(KeyboardIdle=GLib.Variant("b", dimmed))

    def _sync_activity(self) -> None:
        """Watch the keyboards only while an idle timeout needs it."""
        k = self.keyboard
        wanted = k is not None and k.enabled and k.idle_timeout_s > 0
        if wanted and not self.activity.running:
            self.activity.start()
        elif not wanted and self.activity.running:
            self.activity.stop()
            print("gigactld: stopped watching keyboards", flush=True)

    def _leave_keyboard_steady(self) -> None:
        """On a clean stop, put the keys back to the user's plain colour, so a
        stopped daemon never strands them dark (idle) or mid-breath."""
        if self.keyboard is None:
            return
        self.activity.stop()
        try:
            self.keyboard.apply(self.ec)
        except Exception as exc:
            print(f"gigactld: keyboard restore on shutdown failed: {exc}", flush=True)

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
        if going_to_sleep:
            # No effect frames while the machine suspends; resume redraws.
            if self._light_source:
                GLib.source_remove(self._light_source)
                self._light_source = 0
        else:
            self._apply_keyboard("resume")

    def _on_name_acquired(self, conn, name: str) -> None:
        print(f"gigactld: owning {name} (EC backend: {self.ec.backend.name})", flush=True)
        # Say it here as well as on the property: a user asking "why is nothing
        # happening" reaches for systemctl status long before they think to
        # inspect a D-Bus property.
        status = self.ec_status()
        if status:
            print(f"gigactld: {status}", flush=True)

    def _on_name_lost(self, conn, name: str) -> None:
        print(f"gigactld: lost name {name}; is another instance running?", flush=True)
        self._loop.quit()

    # --- D-Bus surface -------------------------------------------------------
    def _get_property(self, conn, sender, path, iface, prop):
        if prop == "DaemonVersion":
            return GLib.Variant("s", __version__)
        if prop == "ActiveProfile":
            return GLib.Variant("s", self.engine.profile)
        if prop == "EcStatus":
            return GLib.Variant("s", self.ec_status())
        if prop == "Telemetry":
            try:
                t = read_telemetry(self.ec)
            except Exception as exc:  # reached from the GLib dispatcher
                print(f"gigactld: telemetry read failed: {exc}", flush=True)
                return None
            return GLib.Variant("(uuuuuu)",
                                (t.cpu_temp, t.gpu_temp, t.fan1_rpm, t.fan2_rpm,
                                 t.fan1_duty_pct, t.fan2_duty_pct))
        if prop == "KeyboardState":
            return _keyboard_variant(self.keyboard)
        if prop == "KeyboardEffects":
            return _effects_variant(self.keyboard)
        if prop == "KeyboardIdle":
            return GLib.Variant("b", self._kbd_dimmed)
        if prop == "Curves":
            return _curves_variant(self.engine)
        return None

    def ec_status(self) -> str:
        """Why the EC is not doing as it is told, or "" when it is.

        Asked fresh rather than remembered, so a machine that is fixed while the
        daemon runs stops complaining on its own.
        """
        # A read-only EC outranks a generic write failure: both are true at
        # once when the module lacks write support, and only one of them tells
        # the user what to do about it.
        return self.ec.unwritable_reason() or self._engine_fault or ""

    def _sync_ec_status(self) -> None:
        """Log and announce the EC status, but only when it changes.

        A fault that persists is not news on every tick; a transition is. The
        announcement matters as much as the log — a window is often open while
        this happens, and should not need restarting to notice.
        """
        status = self.ec_status()
        if status == self._last_ec_status:
            return
        self._last_ec_status = status
        print(f"gigactld: {status}" if status
              else "gigactld: the EC is accepting writes again", flush=True)
        self._notify_properties(EcStatus=GLib.Variant("s", status))

    def _notify_properties(self, **changed: GLib.Variant) -> None:
        """Emit ``PropertiesChanged`` so a running GUI or tray reflects state it
        did not set itself — another client, or a manual duty override flipping
        the profile to 'manual'. (The boot restore needs no emit: it runs before
        the bus name is acquired, so nobody is listening yet.) Best-effort: a
        failed emit must never break the call that triggered it."""
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

    def _notify_fan_state(self) -> None:
        """Profile and curves always change together — one emission covers both."""
        self._notify_properties(
            ActiveProfile=GLib.Variant("s", self.engine.profile),
            Curves=_curves_variant(self.engine))

    def _notify_keyboard(self) -> None:
        self._notify_properties(KeyboardState=_keyboard_variant(self.keyboard),
                                KeyboardEffects=_effects_variant(self.keyboard))

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
            elif method == "SetKeyboardEffect":
                self._set_keyboard_effect(sender, params, invocation)
            elif method == "SetKeyboardIdleTimeout":
                self._set_keyboard_idle_timeout(sender, params, invocation)
            elif method == "SetKeyboardNotifyFlash":
                self._set_keyboard_notify_flash(sender, params, invocation)
            elif method == "FlashKeyboard":
                self._flash_keyboard(sender, invocation)
            else:
                invocation.return_dbus_error(f"{_ERR}.UnknownMethod", method)
        except ec_mod.EcNotWritable as exc:
            # A known cause with a known remedy — worth its own error name so a
            # client can recognise it, and its own message so the user is not
            # handed a bare errno as they were in #25.
            invocation.return_dbus_error(f"{_ERR}.EcNotWritable", str(exc))
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
        self._notify_fan_state()
        invocation.return_value(None)

    def _restore_firmware(self, sender, invocation) -> None:
        if not self._authorized(sender, invocation):
            return
        self.engine.set_profile(curve.FIRMWARE)
        for f in fans.FANS:
            fans.restore_auto(self.ec, f)
        self._persist()
        self._notify_fan_state()
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
            self._run_engine(t.cpu_temp, t.gpu_temp, quiet=True)
            self.lighting.note_temperature(t.cpu_temp)
            if self.keyboard is not None and self.keyboard.effect == keyboard.TEMPERATURE:
                self._render_keyboard_quietly()
        except Exception as exc:
            print(f"gigactld: tick failed: {exc}", flush=True)
        self._sync_ec_status()
        return True  # keep the timeout running

    def _run_engine(self, cpu_temp: int, gpu_temp: int, *,
                    quiet: bool = False) -> None:
        """Drive each fan the engine has an opinion about.

        ``quiet`` is for the periodic tick, which must not die on a failure it
        will meet again in two seconds — the fault is recorded and reported
        through ``EcStatus`` instead. A caller acting on a user's request wants
        the exception, so that the request can fail out loud rather than report
        success it did not achieve.
        """
        for fan in fans.FANS:
            src = cpu_temp if fan == fans.CPU_FAN else gpu_temp
            duty = self.engine.decide(fan, src, cpu_temp)
            if duty is None:
                continue
            try:
                fans.set_duty(self.ec, fan, duty)
            except Exception as exc:
                self._engine_fault = (
                    f"the fan profile '{self.engine.profile}' is not being "
                    f"applied: {exc}")
                if not quiet:
                    raise
                return
        self._engine_fault = None

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
        self._notify_fan_state()
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
        self._notify_fan_state()
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

    def _change_keyboard(self, invocation, change) -> None:
        """Apply one user change to the keyboard state and show it at once.

        A user change counts as activity (fiddling with the colour should not
        happen in the dark). If the EC refuses the write, the state is rolled
        back before the error goes out, so what the daemon remembers and
        reports never claims a look the keys are not showing."""
        created = self.keyboard is None
        kbd = self._remember_keyboard()
        before = dataclasses.replace(kbd)
        change(kbd)
        self.lighting.note_activity(time.monotonic())
        try:
            self._render_keyboard()
        except Exception:
            if created:
                self.keyboard = None
            else:
                vars(kbd).update(vars(before))
            raise
        self._kbd_fault = None
        self._sync_activity()
        self._persist()
        self._notify_keyboard()
        invocation.return_value(None)

    def _set_keyboard_color(self, sender, params, invocation) -> None:
        r, g, b = params.unpack()
        if not all(0 <= c <= 255 for c in (r, g, b)):
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs",
                                         f"colour components must be 0-255: {(r, g, b)}")
            return
        if not self._kbd_authorized(sender, invocation):
            return
        self._change_keyboard(invocation, lambda k: k.record_colour(r, g, b))

    def _set_keyboard_brightness(self, sender, params, invocation) -> None:
        (percent,) = params.unpack()
        if not 0 <= percent <= 100:
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs", f"bad percent {percent}")
            return
        if not self._kbd_authorized(sender, invocation):
            return
        self._change_keyboard(invocation, lambda k: k.record_brightness(percent))

    def _set_keyboard_enabled(self, sender, params, invocation) -> None:
        (on,) = params.unpack()
        if not self._kbd_authorized(sender, invocation):
            return
        self._change_keyboard(invocation, lambda k: k.record_enabled(on))

    def _set_keyboard_effect(self, sender, params, invocation) -> None:
        effect, period_s = params.unpack()
        try:
            keyboard.check_effect(effect, period_s)
        except ValueError as exc:
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs", str(exc))
            return
        if not self._kbd_authorized(sender, invocation):
            return
        self._change_keyboard(invocation, lambda k: k.record_effect(effect, period_s))

    def _set_keyboard_idle_timeout(self, sender, params, invocation) -> None:
        (seconds,) = params.unpack()
        try:
            keyboard.check_idle_timeout(seconds)
        except ValueError as exc:
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs", str(exc))
            return
        if not self._kbd_authorized(sender, invocation):
            return
        self._change_keyboard(invocation, lambda k: k.record_idle_timeout(seconds))

    def _set_keyboard_notify_flash(self, sender, params, invocation) -> None:
        (on,) = params.unpack()
        if not self._kbd_authorized(sender, invocation):
            return
        self._change_keyboard(invocation, lambda k: k.record_notify_flash(on))

    def _flash_keyboard(self, sender, invocation) -> None:
        if not self._kbd_authorized(sender, invocation):
            return
        # A flash is not typing: dimmed keys blink and go dark again.
        if self.lighting.start_flash(time.monotonic()):
            self._render_keyboard()
        invocation.return_value(None)

    def stop(self) -> None:
        self._loop.quit()
