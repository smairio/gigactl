"""The tools that write the EC: each asks first and touches nothing when the
answer is no, and keyboard_check puts the backlight back afterwards.

Every test hands the tools a fake EC, so even a tool that ignored the guard
could not reach real hardware from here.
"""
from __future__ import annotations

import importlib
import pathlib
import re
import sys

import pytest

import guard

TOOLS = pathlib.Path(__file__).resolve().parent

# Every tool that writes the EC, with the arguments it needs to get going.
WRITERS = {
    "fan_check": ["--fan", "1"],
    "keyboard_check": [],
    "daemon_smoke": [],
}


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_a_tool_touches_nothing_when_the_guard_refuses(name, monkeypatch,
                                                         fake_ec, capsys):
    if name == "daemon_smoke":
        pytest.importorskip("gi")  # it runs the daemon's own Daemon class
    tool = importlib.import_module(name)
    monkeypatch.setattr(tool, "EmbeddedController", lambda *a, **k: fake_ec)
    monkeypatch.setattr(guard, "refusal",
                        lambda **_: "the gigactl daemon is running")
    monkeypatch.setattr(sys, "argv", [f"{name}.py", *WRITERS[name]])

    assert tool.main() == 1
    assert fake_ec.reads == [] and fake_ec.writes == []
    assert "the gigactl daemon is running" in capsys.readouterr().err


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_a_read_only_ec_is_refused_before_anything_is_written(name, monkeypatch,
                                                                fake_ec, capsys):
    """ec_sys loaded without write support (#25): say so up front, rather than
    die on the first write with a traceback."""
    if name == "daemon_smoke":
        pytest.importorskip("gi")
    tool = importlib.import_module(name)
    fake_ec.unwritable_reason = lambda: "the kernel's EC interface is read-only"
    monkeypatch.setattr(tool, "EmbeddedController", lambda *a, **k: fake_ec)
    monkeypatch.setattr(guard, "refusal", lambda **_: None)
    monkeypatch.setattr(sys, "argv", [f"{name}.py", *WRITERS[name]])

    assert tool.main() == 1
    assert fake_ec.reads == [] and fake_ec.writes == []
    assert "read-only" in capsys.readouterr().err


def test_every_tool_that_writes_the_ec_is_held_to_the_guard():
    """A new tool that writes the EC has to join WRITERS above, and so be
    tested to ask first. ec_read.py only reads, and must not need to."""
    writes = re.compile(r"\b(set_duty|restore_auto|set_color|set_brightness|"
                        r"set_enabled|apply|write_u8|Daemon)\(")
    found = {script.stem for script in TOOLS.glob("*.py")
             if not script.name.startswith("test_")
             and script.name not in ("conftest.py", "guard.py")
             and writes.search(script.read_text())}
    assert found == set(WRITERS)


# --- keyboard_check puts the backlight back --------------------------------------
#
# The EC cannot be asked what the backlight shows (its mirror fields read back
# stale, docs/PROTOCOL.md), so "back" means what the daemon itself re-applies
# when it starts: its saved backlight, or the firmware default if it saved none.

COLOUR, BRIGHTNESS = 0x03, 0x06  # sub-commands of the 0xCA doorbell


def last_sent(fake_ec, sub):
    """The parameters of the last 0xCA command with this sub-command."""
    return [params for doorbell, s, params in fake_ec.commands
            if doorbell == 0xCA and s == sub][-1]


def test_it_puts_back_the_colour_and_brightness_the_daemon_saved(fake_ec,
                                                                 tmp_path):
    import keyboard_check

    saved = tmp_path / "state.json"
    saved.write_text('{"profile": "balanced", "keyboard": {"enabled": true, '
                     '"r": 255, "g": 0, "b": 0, "brightness": 40}}')
    keyboard_check.check(fake_ec, keyboard_check.restore_target(str(saved)),
                         pause=lambda _s: None)
    assert (0, 255, 102) in [p for d, s, p in fake_ec.commands
                             if (d, s) == (0xCA, COLOUR)], "orange was shown"
    assert last_sent(fake_ec, COLOUR) == (0, 255, 0)   # red, in B,R,G order
    assert last_sent(fake_ec, BRIGHTNESS) == (102,)    # 40 % of 255


@pytest.mark.parametrize("saved", [None, '{"profile": "quiet"}'],
                         ids=["no daemon state", "a fan-only daemon state"])
def test_with_no_saved_backlight_it_puts_back_the_firmware_default(
        fake_ec, tmp_path, saved):
    import keyboard_check

    path = tmp_path / "state.json"
    if saved is not None:
        path.write_text(saved)
    keyboard_check.check(fake_ec, keyboard_check.restore_target(str(path)),
                         pause=lambda _s: None)
    assert last_sent(fake_ec, COLOUR) == (200, 0, 0)   # factory blue (0,0,200)
    assert last_sent(fake_ec, BRIGHTNESS) == (255,)    # full brightness


def test_it_puts_the_backlight_back_even_when_interrupted(fake_ec, tmp_path):
    import keyboard_check

    def ctrl_c(_seconds):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        keyboard_check.check(
            fake_ec, keyboard_check.restore_target(str(tmp_path / "none")),
            pause=ctrl_c)
    assert last_sent(fake_ec, COLOUR) == (200, 0, 0)
    assert last_sent(fake_ec, BRIGHTNESS) == (255,)
