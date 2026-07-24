"""Keyboard backlight protocol — the mailbox write sequences, TDD'd against a
recording fake EC (no hardware)."""
from contextlib import nullcontext

import pytest

from gigactld import keyboard as kb
from gigactld import mailbox


class RecordingEc:
    def __init__(self):
        self.writes = []  # (offset, value) in order

    def write_u8(self, off, val):
        self.writes.append((off, val))

    def transaction(self):
        return nullcontext()


def test_enable_sends_master_enable_doorbell():
    ec = RecordingEc()
    kb.set_enabled(ec, True)
    assert ec.writes == [
        (mailbox.FDAT, kb.ENABLE_SUB),
        (mailbox.FBUF, kb.ENABLE_ON),
        (mailbox.FCMD, kb.DOORBELL_ENABLE),
    ]


def test_disable_uses_off_value():
    ec = RecordingEc()
    kb.set_enabled(ec, False)
    assert (mailbox.FBUF, kb.ENABLE_OFF) in ec.writes
    assert ec.writes[-1] == (mailbox.FCMD, kb.DOORBELL_ENABLE)


def test_set_color_enables_first_then_writes_BRG_order():
    ec = RecordingEc()
    kb.set_color(ec, r=255, g=0, b=0)  # RED
    assert (mailbox.FCMD, kb.DOORBELL_ENABLE) in ec.writes  # master-enable first
    tail = ec.writes[-5:]
    assert tail == [
        (mailbox.FDAT, kb.SUB_COLOR),
        (mailbox.FBUF, 0),    # blue
        (mailbox.FBF1, 255),  # red
        (mailbox.FBF2, 0),    # green
        (mailbox.FCMD, kb.DOORBELL_KB),
    ]


def test_set_color_clamps_components():
    ec = RecordingEc()
    kb.set_color(ec, r=999, g=-5, b=300)
    tail = ec.writes[-5:]
    assert tail[1] == (mailbox.FBUF, 255)  # blue clamped
    assert tail[2] == (mailbox.FBF1, 255)  # red clamped
    assert tail[3] == (mailbox.FBF2, 0)    # green clamped


def test_set_brightness_writes_level_after_enable():
    ec = RecordingEc()
    kb.set_brightness(ec, 100)
    assert (mailbox.FCMD, kb.DOORBELL_ENABLE) in ec.writes  # enabled first
    assert ec.writes[-3:] == [
        (mailbox.FDAT, kb.SUB_BRIGHT),
        (mailbox.FBUF, 255),
        (mailbox.FCMD, kb.DOORBELL_KB),
    ]


def test_brightness_out_of_range_rejected():
    ec = RecordingEc()
    with pytest.raises(ValueError):
        kb.set_brightness(ec, 150)


# --- KeyboardState: the persisted last-set state, re-applied at boot/resume ---

def test_keyboard_state_dict_roundtrip():
    s = kb.KeyboardState(enabled=True, r=255, g=128, b=0, brightness_pct=60)
    assert kb.KeyboardState.from_dict(s.to_dict()) == s


def test_keyboard_state_from_dict_fills_defaults():
    s = kb.KeyboardState.from_dict({})  # nothing saved
    assert s == kb.KeyboardState()      # firmware default: enabled blue, full
    assert (s.b, s.brightness_pct, s.enabled) == (255, 100, True)


def test_keyboard_state_from_dict_clamps_brightness():
    assert kb.KeyboardState.from_dict({"brightness": 999}).brightness_pct == 100
    assert kb.KeyboardState.from_dict({"brightness": -5}).brightness_pct == 0


def test_apply_enabled_writes_colour_then_brightness():
    ec = RecordingEc()
    kb.KeyboardState(enabled=True, r=255, g=0, b=0, brightness_pct=50).apply(ec)
    # colour block (B,R,G) then brightness block; each master-enables first
    assert (mailbox.FCMD, kb.DOORBELL_ENABLE) in ec.writes
    assert (mailbox.FDAT, kb.SUB_COLOR) in ec.writes
    assert ec.writes[-3:] == [
        (mailbox.FDAT, kb.SUB_BRIGHT),
        (mailbox.FBUF, mailbox.pct_to_byte(50)),
        (mailbox.FCMD, kb.DOORBELL_KB),
    ]


def test_apply_disabled_only_disables():
    ec = RecordingEc()
    kb.KeyboardState(enabled=False, r=255, g=0, b=0).apply(ec)
    assert ec.writes == [
        (mailbox.FDAT, kb.ENABLE_SUB),
        (mailbox.FBUF, kb.ENABLE_OFF),
        (mailbox.FCMD, kb.DOORBELL_ENABLE),
    ]
