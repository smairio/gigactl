"""Fan control logic — the doorbell writes and pure helpers, TDD'd against a
recording fake EC (no hardware)."""
from contextlib import nullcontext

import pytest

from gigactld import fans, mailbox


class RecordingEc:
    def __init__(self, regs=None):
        self.regs = dict(regs or {})
        self.writes = []  # (offset, value) in order

    def read_u8(self, off):
        return self.regs.get(off, 0)

    def write_u8(self, off, val):
        self.writes.append((off, val))
        self.regs[off] = val

    def transaction(self):
        return nullcontext()


def test_set_duty_writes_doorbell_sequence_in_order():
    ec = RecordingEc()
    fans.set_duty(ec, fan=1, pct=70)
    assert ec.writes == [
        (mailbox.FDAT, 1),
        (mailbox.FBUF, 178),   # 70% -> 178
        (mailbox.FCMD, fans.DOORBELL),
    ]


def test_set_duty_fan2_targets_gpu():
    ec = RecordingEc()
    fans.set_duty(ec, fan=2, pct=100)
    assert ec.writes == [(mailbox.FDAT, 2), (mailbox.FBUF, 255), (mailbox.FCMD, fans.DOORBELL)]


def test_restore_auto_uses_ff_sentinel():
    ec = RecordingEc()
    fans.restore_auto(ec, fan=1)
    assert ec.writes == [
        (mailbox.FDAT, fans.FAN_AUTO),
        (mailbox.FBUF, 1),
        (mailbox.FCMD, fans.DOORBELL),
    ]


def test_invalid_fan_rejected():
    ec = RecordingEc()
    with pytest.raises(ValueError):
        fans.set_duty(ec, fan=3, pct=50)


def test_expand_maps_selector_to_real_fans():
    assert fans.expand(fans.BOTH_FANS) == (fans.CPU_FAN, fans.GPU_FAN)
    assert fans.expand(1) == (1,)
    assert fans.expand(2) == (2,)


def test_expand_rejects_bad_selector():
    for bad in (3, -1, 99):
        with pytest.raises(ValueError):
            fans.expand(bad)


def test_read_duty_reads_the_right_register():
    ec = RecordingEc({0xCE: 180, 0xCF: 64})
    assert fans.read_duty(ec, 1) == 180
    assert fans.read_duty(ec, 2) == 64


def test_duty_matches_is_symmetric():
    assert fans.duty_matches(target_raw=178, observed_raw=178) is True
    assert fans.duty_matches(target_raw=178, observed_raw=170) is True   # within tol
    assert fans.duty_matches(target_raw=178, observed_raw=64) is False   # rejected, held high
    assert fans.duty_matches(target_raw=26, observed_raw=160) is False   # rejected downward
