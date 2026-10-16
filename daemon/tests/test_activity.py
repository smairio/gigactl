"""Keyboard activity detection: which devices count as keyboards, and that the
watcher reports activity, drains (never parses) the data, and copes with
devices coming and going. Pipes stand in for /dev/input/eventN."""
import os

import gi

gi.require_version("Gio", "2.0")  # GLib comes with it (see packaging)
from gi.repository import GLib  # noqa: E402

from gigactld import activity  # noqa: E402

# Trimmed from a real G6 KF: the built-in keyboard, the power button and lid
# switch (kbd handler, no auto-repeat), and a touchpad (no kbd handler).
DEVICES = """\
I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
H: Handlers=kbd event2
B: EV=3

I: Bus=0011 Vendor=0001 Product=0001 Version=ab83
N: Name="AT Translated Set 2 keyboard"
H: Handlers=sysrq kbd leds event3
B: EV=120013

I: Bus=0019 Vendor=0000 Product=0005 Version=0000
N: Name="Lid Switch"
H: Handlers=event1
B: EV=21

I: Bus=0018 Vendor=093a Product=0255 Version=0100
N: Name="PNP0C50:00 093A:0255 Touchpad"
H: Handlers=mouse0 event7
B: EV=1b

I: Bus=0003 Vendor=046d Product=c52b Version=0111
N: Name="Logitech USB Keyboard"
H: Handlers=sysrq kbd leds event12
B: EV=120013
"""


def test_finds_only_real_keyboards():
    assert activity.keyboard_nodes(DEVICES) == ["/dev/input/event3",
                                                 "/dev/input/event12"]


def test_tolerates_garbage():
    assert activity.keyboard_nodes("H: Handlers=kbd event1\nB: EV=zz\n") == []
    assert activity.keyboard_nodes("") == []


class _Pipes:
    """An opener that hands out the read end of a fresh pipe per device."""

    def __init__(self):
        self.write_ends = {}

    def __call__(self, path):
        r, w = os.pipe()
        os.set_blocking(r, False)
        self.write_ends[path] = w
        return r


def _pump():
    ctx = GLib.MainContext.default()
    for _ in range(20):
        ctx.iteration(False)


def test_reports_activity_and_drains(tmp_path):
    devices = tmp_path / "devices"
    devices.write_text(DEVICES)
    pipes = _Pipes()
    hits = []
    w = activity.ActivityWatcher(lambda: hits.append(1),
                                 devices_path=str(devices), opener=pipes)
    w.start()
    try:
        assert w.watched == ["/dev/input/event12", "/dev/input/event3"]
        os.write(pipes.write_ends["/dev/input/event3"], b"x" * 48)
        _pump()
        assert hits  # activity seen
        fd = w._watched["/dev/input/event3"][0]
        try:
            os.read(fd, 10)
            drained = False
        except BlockingIOError:
            drained = True
        assert drained  # the bytes were consumed, not left to re-fire
    finally:
        w.stop()
    assert not w.running and w.watched == []


def test_rescan_follows_unplug_and_replug(tmp_path):
    devices = tmp_path / "devices"
    devices.write_text(DEVICES)
    pipes = _Pipes()
    w = activity.ActivityWatcher(lambda: None, devices_path=str(devices), opener=pipes)
    w.rescan()
    usb_only_gone = DEVICES.split("\n\nI: Bus=0003")[0] + "\n"
    devices.write_text(usb_only_gone)
    w.rescan()
    assert w.watched == ["/dev/input/event3"]
    devices.write_text(DEVICES)
    w.rescan()
    assert "/dev/input/event12" in w.watched
    w.stop()


def test_hangup_drops_the_device(tmp_path):
    devices = tmp_path / "devices"
    devices.write_text(DEVICES)
    pipes = _Pipes()
    hits = []
    w = activity.ActivityWatcher(lambda: hits.append(1),
                                 devices_path=str(devices), opener=pipes)
    w.rescan()
    os.close(pipes.write_ends["/dev/input/event3"])  # device vanished
    _pump()
    assert "/dev/input/event3" not in w.watched
    assert hits == []  # a hang-up is not a key press
    w.stop()


def test_unopenable_device_is_skipped(tmp_path):
    devices = tmp_path / "devices"
    devices.write_text(DEVICES)

    def deny(path):
        raise PermissionError(path)

    w = activity.ActivityWatcher(lambda: None, devices_path=str(devices), opener=deny)
    w.rescan()
    assert w.watched == []
