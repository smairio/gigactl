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
    # the bug the review caught: a rejected DOWNWARD write (target low, EC holds
    # the fan high) must read as NOT matched
    assert fans.duty_matches(target_raw=26, observed_raw=160) is False
