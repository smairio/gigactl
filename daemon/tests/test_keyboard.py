"""Keyboard backlight protocol — the mailbox write sequences are TDD'd here
against a recording fake EC (no hardware)."""
from contextlib import nullcontext

import pytest

from gigactld import keyboard as kb


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
        (kb.FDAT, kb.ENABLE_SUB),
        (kb.FBUF, kb.ENABLE_ON),
        (kb.FBF1, 0),
        (kb.FBF2, 0),
        (kb.FCMD, kb.DOORBELL_ENABLE),
    ]


def test_disable_uses_off_value():
    ec = RecordingEc()
    kb.set_enabled(ec, False)
    assert (kb.FBUF, kb.ENABLE_OFF) in ec.writes
    assert ec.writes[-1] == (kb.FCMD, kb.DOORBELL_ENABLE)


def test_set_color_enables_first_then_writes_BRG_order():
    ec = RecordingEc()
    kb.set_color(ec, r=255, g=0, b=0)  # RED
    # a master-enable (0xC4) must precede the color command
    assert (kb.FCMD, kb.DOORBELL_ENABLE) in ec.writes
    # the color command: EC wants Blue, Red, Green in FBUF/FBF1/FBF2
    tail = ec.writes[-5:]
    assert tail == [
        (kb.FDAT, kb.SUB_COLOR),
        (kb.FBUF, 0),    # blue
        (kb.FBF1, 255),  # red
        (kb.FBF2, 0),    # green
        (kb.FCMD, kb.DOORBELL_KB),
    ]


def test_set_color_clamps_components():
    ec = RecordingEc()
    kb.set_color(ec, r=999, g=-5, b=300)
    tail = ec.writes[-5:]
    assert tail[1] == (kb.FBUF, 255)  # blue clamped
    assert tail[2] == (kb.FBF1, 255)  # red clamped
    assert tail[3] == (kb.FBF2, 0)    # green clamped


def test_brightness_percent_maps_to_0_255_level():
    assert kb.pct_to_level(0) == 0
    assert kb.pct_to_level(100) == 255
    assert kb.pct_to_level(50) == 128


def test_set_brightness_writes_level_after_enable():
    ec = RecordingEc()
    kb.set_brightness(ec, 100)
    assert (kb.FCMD, kb.DOORBELL_ENABLE) in ec.writes  # enabled first
    tail = ec.writes[-5:]
    assert tail == [
        (kb.FDAT, kb.SUB_BRIGHT),
        (kb.FBUF, 255),
        (kb.FBF1, 0),
        (kb.FBF2, 0),
        (kb.FCMD, kb.DOORBELL_KB),
    ]


def test_brightness_out_of_range_rejected():
    ec = RecordingEc()
    with pytest.raises(ValueError):
        kb.set_brightness(ec, 150)
