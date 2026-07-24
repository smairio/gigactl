"""The Clevo EC command mailbox — shared by every control module.

A command is written parameter-first, command byte (the *doorbell*) last, all
inside one EC transaction so the sequence is atomic. Only the parameters a
command needs are written; the EC ignores leftover scratch bytes.
See ``docs/PROTOCOL.md``.
"""
from __future__ import annotations

# mailbox registers
FCMD = 0xF8   # command / doorbell — written LAST, triggers execution
FDAT = 0xF9   # sub-command
FBUF = 0xFA   # parameter 1
FBF1 = 0xFB   # parameter 2
FBF2 = 0xFC   # parameter 3
FBF3 = 0xFD   # parameter 4
_PARAM_REGS = (FBUF, FBF1, FBF2, FBF3)


def command(ec, sub: int, params=(), *, doorbell: int) -> None:
    """Issue one mailbox command atomically: FDAT=sub, params into FBUF.., then
    the doorbell into FCMD."""
    if len(params) > len(_PARAM_REGS):
        raise ValueError(f"too many mailbox params: {len(params)}")
    with ec.transaction():
        ec.write_u8(FDAT, sub)
        for reg, val in zip(_PARAM_REGS, params):
            ec.write_u8(reg, val)
        ec.write_u8(FCMD, doorbell)


def pct_to_byte(pct: int) -> int:
    """Scale a 0-100 percentage to a 0-255 EC byte."""
    return round(max(0, min(100, pct)) * 255 / 100)
