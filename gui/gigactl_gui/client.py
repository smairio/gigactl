"""D-Bus client for the root daemon (system bus).

Subscribes to the ``Telemetry`` signal, tracks whether the daemon is running
(name owner present), and reads the ``DaemonVersion`` property. The GUI is a
pure consumer here — it issues no control calls in the read-only Overview.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

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


class DaemonClient:
    """Live view of the daemon. Callbacks fire on the GLib main loop:

    - ``on_telemetry(Telemetry)`` — every poll (~2 s).
    - ``on_availability(available: bool)`` — daemon appeared / vanished.
    """

    def __init__(self,
                 on_telemetry: Callable[[Telemetry], None] | None = None,
                 on_availability: Callable[[bool], None] | None = None) -> None:
        self._on_telemetry = on_telemetry
        self._on_availability = on_availability
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
        self._notify_availability()

    @property
    def available(self) -> bool:
        return bool(self._proxy and self._proxy.get_name_owner())

    def _notify_availability(self) -> None:
        if self._on_availability:
            self._on_availability(self.available)

    def _on_signal(self, _proxy, _sender, signal_name, params) -> None:
        # Runs on the GLib loop — never let an exception escape into the
        # dispatcher; a malformed emit would otherwise crash it.
        if signal_name != "Telemetry" or not self._on_telemetry:
            return
        try:
            self._on_telemetry(Telemetry.from_tuple(params.unpack()))
        except Exception as exc:
            print(f"gigactl-gui: bad telemetry signal: {exc}", flush=True)
