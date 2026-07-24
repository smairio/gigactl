"""Telemetry decode is pure logic — TDD'd here against a fake EC (no hardware)."""
from contextlib import nullcontext

from gigactld.telemetry import rpm_from_period, duty_to_percent, read_telemetry


class FakeEc:
    """In-memory stand-in for EmbeddedController: a 256-byte register file."""
    def __init__(self, data):
        self.data = data

    def read_u8(self, off):
        return self.data[off]

    def transaction(self):
        return nullcontext()


def test_rpm_from_typical_period():
    # 0x036C = 876 ticks -> ~2461 rpm (matches an idle reading on the G6 KF)
    assert abs(rpm_from_period(876) - 2461) <= 2


def test_rpm_zero_period_is_stopped():
    assert rpm_from_period(0) == 0


def test_rpm_sentinel_period_is_stopped():
    # a stopped fan reports 0xFFxx-ish garbage; treat as 0, never divide into a huge rpm
    assert rpm_from_period(0xFFFF) == 0


def test_duty_percent_bounds():
    assert duty_to_percent(0) == 0
    assert duty_to_percent(255) == 100
    assert duty_to_percent(180) == 70  # 180*100//255


def test_read_telemetry_maps_registers():
    data = [0] * 256
    data[0x07] = 91            # CPU temp
    data[0x0A] = 73            # GPU temp
    data[0xCE] = 180           # fan1 duty raw -> 70%
    data[0xCF] = 64            # fan2 duty raw -> 25%
    data[0xD0], data[0xD1] = 0x03, 0x6C   # fan1 period 876 -> ~2461 rpm
    data[0xD2], data[0xD3] = 0x03, 0x87   # fan2 period 903 -> ~2388 rpm

    t = read_telemetry(FakeEc(data))

    assert t.cpu_temp == 91
    assert t.gpu_temp == 73
    assert t.fan1_duty_pct == 70
    assert t.fan2_duty_pct == 25
    assert 2450 <= t.fan1_rpm <= 2470
    assert 2380 <= t.fan2_rpm <= 2400
