"""Fan control — set a fan's duty or hand it back to firmware, via the shared
EC mailbox (see ``mailbox.py`` and ``docs/PROTOCOL.md``)."""
from __future__ import annotations

from .mailbox import command, pct_to_byte

DOORBELL = 0xC1     # fan set / auto command
FAN_AUTO = 0xFF     # FDAT sentinel: hand a fan back to the firmware curve

# current-duty readback registers (0-255)
DUTY_REG = {1: 0xCE, 2: 0xCF}

CPU_FAN = 1
GPU_FAN = 2
BOTH_FANS = 0
FANS = (CPU_FAN, GPU_FAN)  # the real fans, in order

# the EC duty register snaps to the commanded value; small margin absorbs rounding
_DUTY_TOLERANCE = 16


def expand(selector: int) -> tuple[int, ...]:
    """Map a fan selector to the real fans it addresses (0=both, 1=CPU, 2=GPU)."""
    if selector == BOTH_FANS:
        return FANS
    if selector in FANS:
        return (selector,)
    raise ValueError(f"invalid fan selector {selector!r}; expected 0, 1 or 2")


def set_duty(ec, fan: int, pct: int) -> None:
    """Set a single fan (1=CPU, 2=GPU) to ``pct``% duty."""
    if fan not in FANS:
        raise ValueError(f"invalid fan {fan!r}; expected one of {FANS}")
    command(ec, fan, (pct_to_byte(pct),), doorbell=DOORBELL)


def restore_auto(ec, fan: int) -> None:
    """Hand a single fan back to the firmware curve."""
    if fan not in FANS:
        raise ValueError(f"invalid fan {fan!r}; expected one of {FANS}")
    command(ec, FAN_AUTO, (fan,), doorbell=DOORBELL)


def read_duty(ec, fan: int) -> int:
    """Current commanded duty (0-255) for ``fan``, read in one transaction."""
    with ec.transaction():
        return ec.read_u8(DUTY_REG[fan])


def duty_matches(target_raw: int, observed_raw: int) -> bool:
    """Did the EC take the duty write? True iff the readback is within tolerance
    of the target in either direction — a rejected write (up or down) leaves the
    register at the firmware value, far from target."""
    return abs(observed_raw - target_raw) <= _DUTY_TOLERANCE
