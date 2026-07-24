"""Shared EC mailbox helper — command sequencing and the pct scaler."""
from contextlib import nullcontext

import pytest

from gigactld import mailbox


class RecordingEc:
    def __init__(self):
        self.writes = []

    def write_u8(self, off, val):
        self.writes.append((off, val))

    def transaction(self):
        return nullcontext()


def test_command_writes_sub_params_then_doorbell():
    ec = RecordingEc()
    mailbox.command(ec, 0x03, (10, 20, 30), doorbell=0xCA)
    assert ec.writes == [
        (mailbox.FDAT, 0x03),
        (mailbox.FBUF, 10),
        (mailbox.FBF1, 20),
        (mailbox.FBF2, 30),
        (mailbox.FCMD, 0xCA),
    ]


def test_command_writes_only_the_params_given():
    ec = RecordingEc()
    mailbox.command(ec, 0x0C, (0x3F,), doorbell=0xC4)
    assert ec.writes == [
        (mailbox.FDAT, 0x0C),
        (mailbox.FBUF, 0x3F),
        (mailbox.FCMD, 0xC4),
    ]


def test_command_rejects_too_many_params():
    ec = RecordingEc()
    with pytest.raises(ValueError):
        mailbox.command(ec, 0, (1, 2, 3, 4, 5), doorbell=0xC1)


def test_pct_to_byte():
    assert mailbox.pct_to_byte(0) == 0
    assert mailbox.pct_to_byte(100) == 255
    assert mailbox.pct_to_byte(70) == 178
    assert mailbox.pct_to_byte(-5) == 0     # clamped
    assert mailbox.pct_to_byte(150) == 255  # clamped
