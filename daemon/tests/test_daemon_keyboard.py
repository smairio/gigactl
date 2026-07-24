"""Daemon-level keyboard persistence: boot restore + resume re-apply, driven
against the shared fakes in ``daemon/conftest.py`` (no bus, no hardware)."""
import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from gigactld import keyboard as kb  # noqa: E402
from gigactld import mailbox, state  # noqa: E402


def _sleep_signal(going_to_sleep: bool):
    return (None, None, None, None, None, GLib.Variant("(b)", (going_to_sleep,)))


def test_resume_edge_reapplies_keyboard(make_daemon):
    d = make_daemon()
    d.keyboard = kb.KeyboardState(enabled=True, r=255, g=0, b=0, brightness_pct=50)
    d.ec.writes.clear()
    d._on_prepare_for_sleep(*_sleep_signal(going_to_sleep=False))
    assert (mailbox.FDAT, kb.SUB_COLOR) in d.ec.writes  # colour re-applied on resume


def test_sleep_edge_does_not_reapply(make_daemon):
    d = make_daemon()
    d.keyboard = kb.KeyboardState(enabled=True, r=255, g=0, b=0)
    d.ec.writes.clear()
    d._on_prepare_for_sleep(*_sleep_signal(going_to_sleep=True))
    assert d.ec.writes == []  # going to sleep: nothing to do


def test_boot_restores_saved_keyboard(make_daemon, tmp_path):
    p = str(tmp_path / "state.json")
    ks = kb.KeyboardState(enabled=True, r=0, g=255, b=0, brightness_pct=80)
    state.save(p, state.snapshot(make_daemon().engine, ks))

    d = make_daemon()
    d.state_path = p
    d._restore_saved_state()
    assert d.keyboard == ks
    assert (mailbox.FDAT, kb.SUB_COLOR) in d.ec.writes  # applied at boot


def test_fan_only_persist_writes_no_keyboard_section_and_boot_untouched(
        make_daemon, tmp_path):
    # Drive the REAL persist path a fan-only user takes: keyboard never set, so
    # self.keyboard stays None. (A test that hand-built snapshot(engine) with no
    # kbd arg would pass even with the bug — this one wouldn't.)
    p = str(tmp_path / "state.json")
    d1 = make_daemon()
    d1.state_path = p
    d1.engine.set_profile("balanced")
    d1._persist()
    assert "keyboard" not in state.load(p)  # fan-only => no keyboard section

    d2 = make_daemon()
    d2.state_path = p
    d2._restore_saved_state()
    # nothing saved for the keyboard => don't force firmware default onto it
    assert not any(off == mailbox.FDAT and val == kb.SUB_COLOR for off, val in d2.ec.writes)


def test_keyboard_set_creates_and_persists_section(make_daemon, tmp_path):
    p = str(tmp_path / "state.json")
    d = make_daemon()
    d.state_path = p
    assert d.keyboard is None  # lazy: nothing until the user sets something
    d._remember_keyboard().record_colour(255, 0, 0)
    d._persist()
    assert state.load(p)["keyboard"]["r"] == 255


def test_resume_without_prior_set_is_noop(make_daemon):
    d = make_daemon()  # fresh: keyboard never set
    d._on_prepare_for_sleep(*_sleep_signal(going_to_sleep=False))
    assert d.ec.writes == []  # nothing remembered => nothing to re-drive
