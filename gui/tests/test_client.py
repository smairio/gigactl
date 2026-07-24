"""Telemetry parsing — the pure part of the D-Bus client."""
import pytest

from gigactl_gui.client import Telemetry


def test_from_tuple_maps_all_fields():
    tm = Telemetry.from_tuple((91, 73, 2486, 2411, 27, 30))
    assert (tm.cpu_temp, tm.gpu_temp) == (91, 73)
    assert (tm.fan1_rpm, tm.fan2_rpm) == (2486, 2411)
    assert (tm.fan1_duty_pct, tm.fan2_duty_pct) == (27, 30)


def test_from_tuple_rejects_wrong_arity():
    with pytest.raises(ValueError):
        Telemetry.from_tuple((1, 2, 3))
