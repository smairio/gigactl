"""Fan control — the Clevo EC mailbox doorbell sequence.

Set a fan to a duty, or hand it back to the firmware curve. The mailbox is
written parameter-first, command (doorbell) last; see ``docs/PROTOCOL.md``.
Every sequence runs inside one EC transaction so it stays atomic.
"""
from __future__ import annotations

# mailbox registers
FCMD = 0xF8   # command / doorbell (written last)
FDAT = 0xF9   # sub-command / fan selector
FBUF = 0xFA   # parameter (duty, or fan# for the auto sentinel)
DOORBELL = 0xC1
FAN_AUTO = 0xFF  # FDAT sentinel meaning "hand fan back to firmware"

# current-duty readback registers (0-255)
DUTY_REG = {1: 0xCE, 2: 0xCF}

CPU_FAN = 1
GPU_FAN = 2
BOTH_FANS = 0
FANS = (CPU_FAN, GPU_FAN)  # the real fans, in order

# The EC duty register snaps to the commanded value, so a taken write reads
# back at (near) target and a rejected one stays at the firmware value. A small
# margin absorbs rounding.
_DUTY_TOLERANCE = 16


def pct_to_raw(pct: int) -> int:
    return round(max(0, min(100, pct)) * 255 / 100)


def expand(selector: int) -> tuple[int, ...]:
    """Map a fan selector to the real fans it addresses.

    0 = both, 1 = CPU, 2 = GPU. Raises ValueError on anything else.
    """
    if selector == BOTH_FANS:
        return FANS
    if selector in FANS:
        return (selector,)
    raise ValueError(f"invalid fan selector {selector!r}; expected 0, 1 or 2")


def _doorbell(ec, fdat: int, fbuf: int) -> None:
    """Write one mailbox command atomically: params first, doorbell last."""
    with ec.transaction():
        ec.write_u8(FDAT, fdat)
        ec.write_u8(FBUF, fbuf)
        ec.write_u8(FCMD, DOORBELL)


def set_duty(ec, fan: int, pct: int) -> None:
    """Set a single fan (1=CPU, 2=GPU) to ``pct``% duty."""
    if fan not in FANS:
        raise ValueError(f"invalid fan {fan!r}; expected one of {FANS}")
    _doorbell(ec, fan, pct_to_raw(pct))


def restore_auto(ec, fan: int) -> None:
    """Hand a single fan back to the firmware curve."""
    if fan not in FANS:
        raise ValueError(f"invalid fan {fan!r}; expected one of {FANS}")
    _doorbell(ec, FAN_AUTO, fan)


def read_duty(ec, fan: int) -> int:
    """Current commanded duty (0-255) for ``fan``, read in one transaction."""
    with ec.transaction():
        return ec.read_u8(DUTY_REG[fan])


def duty_matches(target_raw: int, observed_raw: int) -> bool:
    """Did the EC take the duty write? True iff the readback is within
    tolerance of the target in *either* direction — a rejected write (up or
    down) leaves the register at the firmware value, far from target."""
    return abs(observed_raw - target_raw) <= _DUTY_TOLERANCE
