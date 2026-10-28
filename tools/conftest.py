"""Fakes for the tools suite: no hardware, no root, no bus.

The tools take the EC as an argument where it matters, so these tests hand
them a fake one and read back what they did with it.

Fixtures only: no test imports this module by name. When two suites are run in
one pytest invocation, only one module can be called ``conftest`` (see
.github/workflows/release.yml), and a fixture works either way.

Run: ``python3 -m pytest tools -q``
"""
from __future__ import annotations

import pathlib
import sys
from contextlib import contextmanager

import pytest

# gigactld lives in daemon/, which is where the tools themselves import it from.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "daemon"))

from gigactld import mailbox  # noqa: E402


class FakeEc:
    """Records every register access, and any made outside a transaction —
    that is, without the EC lock. Registers nobody wrote read 0."""

    def __init__(self):
        self.regs = {}
        self.reads = []      # offsets, in order
        self.writes = []     # (offset, value), in order
        self.unlocked = []   # accesses made outside a transaction
        self._depth = 0

    @contextmanager
    def transaction(self):
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1

    def read_u8(self, offset):
        if not self._depth:
            self.unlocked.append(("read", offset))
        self.reads.append(offset)
        return self.regs.get(offset, 0)

    def read_u16_be(self, hi, lo):
        return (self.read_u8(hi) << 8) | self.read_u8(lo)

    def write_u8(self, offset, value):
        if not self._depth:
            self.unlocked.append(("write", offset, value))
        self.writes.append((offset, value))
        self.regs[offset] = value

    def unwritable_reason(self):
        return None

    @property
    def commands(self):
        """The writes regrouped into mailbox commands, as (doorbell, sub, params)."""
        found, pending = [], []
        for offset, value in self.writes:
            if offset == mailbox.FCMD:
                sub, *params = pending
                found.append((value, sub, tuple(params)))
                pending = []
            else:
                pending.append(value)
        return found


@pytest.fixture
def fake_ec():
    return FakeEc()
