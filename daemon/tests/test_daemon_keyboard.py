"""Daemon-level keyboard persistence: boot restore + resume re-apply, driven
against a recording fake EC (no bus, no hardware)."""
from contextlib import nullcontext

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from gigactld import keyboard as kb  # noqa: E402
from gigactld import mailbox, state  # noqa: E402
from gigactld.service import Daemon  # noqa: E402


class RecordingEc:
    backend = type("B", (), {"name": "fake"})()

    def __init__(self):
        self.writes = []

    def write_u8(self, off, val):
        self.writes.append((off, val))

    def read_u8(self, off):
        return 0

    def transaction(self):
        return nullcontext()


class DummyAuth:
    def check(self, sender, action):
        return True


def _daemon():
    return Daemon(RecordingEc(), authorizer=DummyAuth())


def _sleep_signal(going_to_sleep: bool):
    return (None, None, None, None, None, GLib.Variant("(b)", (going_to_sleep,)))


def test_resume_edge_reapplies_keyboard():
    d = _daemon()
    d.keyboard = kb.KeyboardState(enabled=True, r=255, g=0, b=0, brightness_pct=50)
    d.ec.writes.clear()
    d._on_prepare_for_sleep(*_sleep_signal(going_to_sleep=False))
    assert (mailbox.FDAT, kb.SUB_COLOR) in d.ec.writes  # colour re-applied on resume


def test_sleep_edge_does_not_reapply():
    d = _daemon()
    d.keyboard = kb.KeyboardState(enabled=True, r=255, g=0, b=0)
    d.ec.writes.clear()
    d._on_prepare_for_sleep(*_sleep_signal(going_to_sleep=True))
    assert d.ec.writes == []  # going to sleep: nothing to do


def test_boot_restores_saved_keyboard(tmp_path):
    p = str(tmp_path / "state.json")
    ks = kb.KeyboardState(enabled=True, r=0, g=255, b=0, brightness_pct=80)
    state.save(p, state.snapshot(_daemon().engine, ks))

    d = _daemon()
    d.state_path = p
    d._restore_saved_state()
    assert d.keyboard == ks
    assert (mailbox.FDAT, kb.SUB_COLOR) in d.ec.writes  # applied at boot


def test_boot_without_keyboard_section_leaves_hardware_untouched(tmp_path):
    p = str(tmp_path / "state.json")
    state.save(p, state.snapshot(_daemon().engine))  # fan-only, no keyboard

    d = _daemon()
    d.state_path = p
    d._restore_saved_state()
    # no keyboard section => don't force firmware default onto the backlight
    assert not any(off == mailbox.FDAT and val == kb.SUB_COLOR for off, val in d.ec.writes)
