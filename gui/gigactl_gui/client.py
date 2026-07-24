"""D-Bus client for the root daemon (system bus).

Subscribes to the ``Telemetry`` signal, tracks whether the daemon is running
(name owner present), reads back the ``ActiveProfile`` / ``KeyboardState``
properties, and issues the control calls. Every control call is asynchronous —
the daemon verifies fan writes on the EC, so a synchronous call would freeze the
UI for the better part of a second — and every failure is reported as one plain
sentence through ``on_error``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import errors  # noqa: E402

BUS_NAME = "io.github.smairio.gigactl"
OBJECT_PATH = "/io/github/smairio/gigactl"
INTERFACE = "io.github.smairio.gigactl.Control"


@dataclass(frozen=True)
class Telemetry:
    """One poll from the daemon — mirrors the ``Telemetry(uuuuuu)`` signal."""
    cpu_temp: int
    gpu_temp: int
    fan1_rpm: int
    fan2_rpm: int
    fan1_duty_pct: int
    fan2_duty_pct: int

    @classmethod
    def from_tuple(cls, values) -> Telemetry:
        if len(values) != 6:
            raise ValueError(f"expected 6 telemetry fields, got {len(values)}")
        return cls(*(int(v) for v in values))


@dataclass(frozen=True)
class KeyboardSnapshot:
    """The daemon's ``KeyboardState`` property — what the controls show as the
    current selection."""
    enabled: bool
    r: int
    g: int
    b: int
    brightness_pct: int

    @classmethod
    def from_tuple(cls, values) -> KeyboardSnapshot:
        if len(values) != 5:
            raise ValueError(f"expected 5 keyboard fields, got {len(values)}")
        enabled, r, g, b, pct = values
        return cls(bool(enabled), int(r), int(g), int(b), int(pct))

    @property
    def rgb(self) -> tuple[int, int, int]:
        return (self.r, self.g, self.b)


class DaemonClient:
    """Live view of the daemon. Callbacks fire on the GLib main loop:

    - ``on_telemetry(Telemetry)`` — every poll (~2 s).
    - ``on_availability(available: bool)`` — daemon appeared / vanished.
    - ``on_state()`` — a property changed (profile or keyboard), including
      changes this GUI did not make.
    - ``on_error(message)`` — a control call failed, already in plain English.
    """

    def __init__(self,
                 on_telemetry: Callable[[Telemetry], None] | None = None,
                 on_availability: Callable[[bool], None] | None = None,
                 on_state: Callable[[], None] | None = None,
                 on_error: Callable[[str], None] | None = None) -> None:
        self._on_telemetry = on_telemetry
        self._on_availability = on_availability
        self._on_state = on_state
        self._on_error = on_error
        self._proxy: Gio.DBusProxy | None = None

    def start(self) -> None:
        """Create the proxy asynchronously so a slow/absent daemon never blocks
        the UI from drawing."""
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SYSTEM,
            Gio.DBusProxyFlags.NONE,
            None,
            BUS_NAME, OBJECT_PATH, INTERFACE,
            None,
            self._on_proxy_ready,
        )

    def _on_proxy_ready(self, _source, result) -> None:
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error as exc:  # pragma: no cover - environment dependent
            print(f"gigactl-gui: could not reach the daemon: {exc}", flush=True)
            self._notify_availability()
            return
        self._proxy.connect("g-signal", self._on_signal)
        self._proxy.connect("notify::g-name-owner", lambda *_: self._notify_availability())
        self._proxy.connect("g-properties-changed", self._on_properties_changed)
        self._notify_availability()
        self._notify_state()

    @property
    def available(self) -> bool:
        return bool(self._proxy and self._proxy.get_name_owner())

    def _safe(self, what: str, callback, *args) -> None:
        """Every callback below is reached from the GLib dispatcher, where an
        escaping exception would take the loop down with it."""
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as exc:
            print(f"gigactl-gui: {what} handler failed: {exc}", flush=True)

    def _notify_availability(self) -> None:
        self._safe("availability", self._on_availability, self.available)

    def _notify_state(self) -> None:
        self._safe("state", self._on_state)

    def _on_properties_changed(self, _proxy, _changed, _invalidated) -> None:
        self._notify_state()

    # --- reading current state ----------------------------------------------
    def active_profile(self) -> str | None:
        if not self._proxy:
            return None
        v = self._proxy.get_cached_property("ActiveProfile")
        return v.get_string() if v is not None else None

    def keyboard_state(self) -> KeyboardSnapshot | None:
        if not self._proxy:
            return None
        v = self._proxy.get_cached_property("KeyboardState")
        if v is None:
            return None
        try:
            return KeyboardSnapshot.from_tuple(v.unpack())
        except (ValueError, TypeError) as exc:
            print(f"gigactl-gui: bad KeyboardState property: {exc}", flush=True)
            return None

    # --- control calls -------------------------------------------------------
    def set_profile(self, name: str) -> None:
        self._call("SetProfile", GLib.Variant("(s)", (name,)))

    def restore_firmware(self) -> None:
        self._call("RestoreFirmware", None)

    def set_keyboard_color(self, r: int, g: int, b: int) -> None:
        self._call("SetKeyboardColor", GLib.Variant("(uuu)", (r, g, b)))

    def set_keyboard_brightness(self, percent: int) -> None:
        self._call("SetKeyboardBrightness", GLib.Variant("(u)", (percent,)))

    def set_keyboard_enabled(self, on: bool) -> None:
        self._call("SetKeyboardEnabled", GLib.Variant("(b)", (on,)))

    def _call(self, method: str, params: GLib.Variant | None) -> None:
        """Fire and forget; failures arrive on ``on_error``. Asynchronous so the
        daemon's fan write-verification never blocks the UI."""
        if not self.available:
            self._report(errors.NO_DAEMON)
            return
        self._proxy.call(method, params, Gio.DBusCallFlags.NONE, -1, None,
                         self._on_call_done, method)

    def _on_call_done(self, proxy, result, method) -> None:
        try:
            proxy.call_finish(result)
        except GLib.Error as exc:
            self._report(errors.human_message(exc.message))
        except Exception as exc:  # never escape into the GLib dispatcher
            print(f"gigactl-gui: {method} failed oddly: {exc}", flush=True)
            self._report(errors.human_message(""))

    def _report(self, message: str) -> None:
        if self._on_error:
            self._on_error(message)
        else:  # pragma: no cover - always wired by the window
            print(f"gigactl-gui: {message}", flush=True)

    def _on_signal(self, _proxy, _sender, signal_name, params) -> None:
        if signal_name != "Telemetry":
            return
        self._safe("telemetry", self._deliver_telemetry, params)

    def _deliver_telemetry(self, params) -> None:
        if self._on_telemetry:
            self._on_telemetry(Telemetry.from_tuple(params.unpack()))
