"""Notice that someone is typing — never what they type.

The idle fade-out needs one fact: *when* a key was last pressed. The daemon
already runs as root, so it reads the kernel's keyboard event devices
(``/dev/input/eventN``) directly. That works the same under Wayland, X11 and
the text console, and needs no help from the desktop session.

Privacy is the rule this module is built around:

* event data is read only to empty the device's queue and is thrown away
  unparsed — no key code is ever decoded, stored, logged or sent anywhere;
* the devices are opened only while an idle timeout is set, and closed the
  moment it is turned off (``Daemon._sync_activity``).

Keyboards are found the way the kernel describes them in
``/proc/bus/input/devices``: a device with the ``kbd`` handler that reports key
events *and* key auto-repeat. Auto-repeat is what separates a real keyboard
from the power button, lid switch and hotkey devices, which also use ``kbd``.
Devices come and go (a USB keyboard, a dock), so the list is re-read every few
seconds while watching.
"""
from __future__ import annotations

import os
from typing import Callable

import gi

gi.require_version("Gio", "2.0")  # GLib comes with it (see packaging)
from gi.repository import GLib  # noqa: E402

PROC_DEVICES = "/proc/bus/input/devices"
DEV_INPUT = "/dev/input"
RESCAN_S = 10

_EV_KEY = 1
_EV_REP = 20
_READ_CHUNK = 4096


def keyboard_nodes(devices_text: str) -> list[str]:
    """The ``/dev/input/eventN`` paths of every real keyboard described in the
    text of ``/proc/bus/input/devices``."""
    found: list[str] = []
    for block in devices_text.split("\n\n"):
        handlers: list[str] = []
        ev_bits = 0
        for line in block.splitlines():
            if line.startswith("H: Handlers="):
                handlers = line.split("=", 1)[1].split()
            elif line.startswith("B: EV="):
                try:
                    ev_bits = int(line.split("=", 1)[1].strip(), 16)
                except ValueError:
                    ev_bits = 0
        if "kbd" not in handlers:
            continue
        if not (ev_bits >> _EV_KEY) & 1 or not (ev_bits >> _EV_REP) & 1:
            continue
        for h in handlers:
            if h.startswith("event") and h[5:].isdigit():
                found.append(os.path.join(DEV_INPUT, h))
    return found


class ActivityWatcher:
    """Calls ``on_activity()`` whenever any keyboard produces an event.

    ``devices_path`` and ``opener`` exist so tests can stand in a fake
    ``/proc`` file and pipes for the event devices.
    """

    def __init__(self, on_activity: Callable[[], None],
                 devices_path: str = PROC_DEVICES,
                 opener: Callable[[str], int] | None = None) -> None:
        self._on_activity = on_activity
        self._devices_path = devices_path
        self._open = opener or (lambda p: os.open(p, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC))
        self._watched: dict[str, tuple[int, int]] = {}  # path -> (fd, GLib source)
        self._rescan_source = 0

    @property
    def running(self) -> bool:
        return bool(self._rescan_source)

    @property
    def watched(self) -> list[str]:
        return sorted(self._watched)

    def start(self) -> None:
        if self.running:
            return
        self.rescan()
        self._rescan_source = GLib.timeout_add_seconds(RESCAN_S, self._on_rescan_timer)
        names = ", ".join(self.watched) or "none found yet"
        print(f"gigactld: watching keyboards for activity ({names})", flush=True)

    def stop(self) -> None:
        if self._rescan_source:
            GLib.source_remove(self._rescan_source)
            self._rescan_source = 0
        for path in list(self._watched):
            self._drop(path)

    def _on_rescan_timer(self) -> bool:
        self.rescan()
        return GLib.SOURCE_CONTINUE

    def rescan(self) -> None:
        try:
            with open(self._devices_path) as f:
                wanted = set(keyboard_nodes(f.read()))
        except OSError as exc:
            print(f"gigactld: cannot list input devices: {exc}", flush=True)
            return
        for path in set(self._watched) - wanted:
            self._drop(path)
        for path in wanted - set(self._watched):
            try:
                fd = self._open(path)
            except OSError as exc:
                print(f"gigactld: cannot watch {path}: {exc}", flush=True)
                continue
            source = GLib.unix_fd_add_full(
                GLib.PRIORITY_DEFAULT, fd,
                GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
                self._on_ready, path)
            self._watched[path] = (fd, source)

    def _drop(self, path: str) -> None:
        fd, source = self._watched.pop(path)
        GLib.source_remove(source)
        try:
            os.close(fd)
        except OSError:
            pass

    def _on_ready(self, fd: int, condition, path: str) -> bool:
        try:
            if condition & (GLib.IOCondition.HUP | GLib.IOCondition.ERR):
                raise OSError("device went away")
            # Drain and discard. The bytes are never looked at.
            while os.read(fd, _READ_CHUNK):
                pass
        except BlockingIOError:
            pass  # queue drained
        except OSError:
            # Unplugged. Forget it; the next rescan picks it up if it returns.
            # The source is removed by returning False, so only close the fd.
            self._watched.pop(path, None)
            try:
                os.close(fd)
            except OSError:
                pass
            return GLib.SOURCE_REMOVE
        try:
            self._on_activity()
        except Exception as exc:  # never let it escape into the GLib loop
            print(f"gigactld: activity handler failed: {exc}", flush=True)
        return GLib.SOURCE_CONTINUE
