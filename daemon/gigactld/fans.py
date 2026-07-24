"""Fan control — the Clevo EC mailbox doorbell sequence.

Set fan N to a duty, or hand it back to the firmware curve. The mailbox is
written parameter-first, command (doorbell) last; see ``docs/PROTOCOL.md``.
All writes run inside one EC transaction so the sequence stays atomic.
"""
from __future__ import annotations

# mailbox registers
FCMD = 0xF8   # command / doorbell (written last)
FDAT = 0xF9   # sub-command / fan selector
FBUF = 0xFA   # parameter (duty, or fan# for the auto sentinel)
DOORBELL = 0xC1
FAN_AUTO = 0xFF  # FDAT sentinel meaning "hand fan back to firmware"

# current-duty readback registers (0-255), for write verification
DUTY_REG = {1: 0xCE, 2: 0xCF}

CPU_FAN = 1
GPU_FAN = 2
BOTH_FANS = 0
_REAL_FANS = (CPU_FAN, GPU_FAN)

# how close the readback must get to the target before we call the write accepted
_DUTY_TOLERANCE = 24


def pct_to_raw(pct: int) -> int:
    pct = max(0, min(100, pct))
    return round(pct * 255 / 100)


def raw_to_pct(raw: int) -> int:
    return max(0, min(100, raw * 100 // 255))


def _validate_fan(fan: int, *, allow_both: bool = False) -> None:
    valid = (BOTH_FANS, *_REAL_FANS) if allow_both else _REAL_FANS
    if fan not in valid:
        raise ValueError(f"invalid fan {fan!r}; expected one of {valid}")


def set_duty(ec, fan: int, pct: int) -> None:
    """Set a single fan (1=CPU, 2=GPU) to ``pct``% duty."""
    _validate_fan(fan)
    raw = pct_to_raw(pct)
    with ec.transaction():
        ec.write_u8(FDAT, fan)
        ec.write_u8(FBUF, raw)
        ec.write_u8(FCMD, DOORBELL)


def restore_auto(ec, fan: int) -> None:
    """Hand a single fan back to the firmware curve."""
    _validate_fan(fan)
    with ec.transaction():
        ec.write_u8(FDAT, FAN_AUTO)
        ec.write_u8(FBUF, fan)
        ec.write_u8(FCMD, DOORBELL)


def duty_accepted(target_raw: int, observed_raw: int) -> bool:
    """Did the EC take a duty write?

    The fan ramps toward the target rather than snapping to it, so we accept
    anything within tolerance *below* the target (still climbing) as well as
    at/above it. A readback far below target means the write was ignored.
    """
    return observed_raw >= target_raw - _DUTY_TOLERANCE
