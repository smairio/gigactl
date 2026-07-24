"""Fan control logic — the doorbell write sequence and the pure conversions
are TDD'd here against a recording fake EC (no hardware)."""
from contextlib import nullcontext

import pytest

from gigactld import fans


class RecordingEc:
    """Records every write in order and serves reads from a register dict."""
    def __init__(self, regs=None):
        self.regs = dict(regs or {})
        self.writes = []  # list of (offset, value)

    def read_u8(self, off):
        return self.regs.get(off, 0)

    def write_u8(self, off, val):
        self.writes.append((off, val))
        self.regs[off] = val

    def transaction(self):
        return nullcontext()


def test_pct_to_raw_endpoints_and_mid():
    assert fans.pct_to_raw(0) == 0
    assert fans.pct_to_raw(100) == 255
    assert fans.pct_to_raw(70) == 178  # round(70*255/100)


def test_pct_to_raw_clamps_out_of_range():
    assert fans.pct_to_raw(-5) == 0
    assert fans.pct_to_raw(150) == 255


def test_set_duty_writes_doorbell_sequence_in_order():
    ec = RecordingEc()
    fans.set_duty(ec, fan=1, pct=70)
    # FDAT(fan), FBUF(raw), then FCMD doorbell LAST
    assert ec.writes == [
        (fans.FDAT, 1),
        (fans.FBUF, 178),
        (fans.FCMD, fans.DOORBELL),
    ]


def test_set_duty_fan2_targets_gpu():
    ec = RecordingEc()
    fans.set_duty(ec, fan=2, pct=100)
    assert ec.writes == [(fans.FDAT, 2), (fans.FBUF, 255), (fans.FCMD, fans.DOORBELL)]


def test_restore_auto_uses_ff_sentinel():
    ec = RecordingEc()
    fans.restore_auto(ec, fan=1)
    assert ec.writes == [
        (fans.FDAT, fans.FAN_AUTO),
        (fans.FBUF, 1),
        (fans.FCMD, fans.DOORBELL),
    ]


def test_invalid_fan_rejected():
    ec = RecordingEc()
    with pytest.raises(ValueError):
        fans.set_duty(ec, fan=3, pct=50)


def test_duty_accepted_tolerates_ramp():
    # the EC ramps toward target; "accepted" means observed is at/above a margin
    # below target (it may still be climbing), not exact equality
    assert fans.duty_accepted(target_raw=178, observed_raw=178) is True
    assert fans.duty_accepted(target_raw=178, observed_raw=170) is True   # within tol
    assert fans.duty_accepted(target_raw=178, observed_raw=64) is False   # untouched/firmware
