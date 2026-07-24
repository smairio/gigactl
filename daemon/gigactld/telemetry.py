"""Decode the EC's raw registers into human numbers.

Register offsets and the RPM constant are the ones proven on the G6 KF and
documented in ``docs/PROTOCOL.md`` (and used by the reference ``gfan`` tool).
"""
from __future__ import annotations

from dataclasses import dataclass

# EC register offsets
CPU_TEMP = 0x07
GPU_TEMP = 0x0A
DUTY_FAN1 = 0xCE
DUTY_FAN2 = 0xCF
TACH1_HI, TACH1_LO = 0xD0, 0xD1  # 16-bit big-endian tach period
TACH2_HI, TACH2_LO = 0xD2, 0xD3

# rpm = RPM_CONST / tach_period (standard Clevo formula)
RPM_CONST = 2_156_220
# a stopped fan reports 0 or an 0xFFxx sentinel; anything at/above this is "stopped"
_STOPPED_AT = 0xFF00


@dataclass(frozen=True)
class Telemetry:
    cpu_temp: int      # °C
    gpu_temp: int      # °C
    fan1_rpm: int
    fan2_rpm: int
    fan1_duty_pct: int
    fan2_duty_pct: int


def rpm_from_period(period: int) -> int:
    if period <= 0 or period >= _STOPPED_AT:
        return 0
    return RPM_CONST // period


def duty_to_percent(raw: int) -> int:
    return max(0, min(100, raw * 100 // 255))


def read_telemetry(ec) -> Telemetry:
    """Read every telemetry register in one locked transaction."""
    with ec.transaction():
        cpu = ec.read_u8(CPU_TEMP)
        gpu = ec.read_u8(GPU_TEMP)
        duty1 = ec.read_u8(DUTY_FAN1)
        duty2 = ec.read_u8(DUTY_FAN2)
        period1 = (ec.read_u8(TACH1_HI) << 8) | ec.read_u8(TACH1_LO)
        period2 = (ec.read_u8(TACH2_HI) << 8) | ec.read_u8(TACH2_LO)
    return Telemetry(
        cpu_temp=cpu,
        gpu_temp=gpu,
        fan1_rpm=rpm_from_period(period1),
        fan2_rpm=rpm_from_period(period2),
        fan1_duty_pct=duty_to_percent(duty1),
        fan2_duty_pct=duty_to_percent(duty2),
    )
