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
