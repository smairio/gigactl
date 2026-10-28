"""Whether the window shows the CPU turbo row, and whether it can be used.

The daemon reports two live properties: ``TurboAvailable`` (does this machine
have the intel_pstate turbo switch at all?) and ``TurboBoost`` (is turbo
allowed right now?). The row is offered only where the switch exists — never a
control for something the machine lacks (PRODUCT.md #3) — and can be used only
when the rest of the controls can.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TurboRow:
    visible: bool
    sensitive: bool
    active: bool


def row(state: tuple[bool, bool] | None, usable: bool) -> TurboRow:
    """The row for the daemon's ``(available, enabled)``.

    ``state`` is None when the daemon is gone or too old to know about turbo.
    ``usable`` is what every other control uses: a supported machine with the
    daemon reachable.
    """
    available, enabled = state or (False, False)
    return TurboRow(visible=available, sensitive=available and usable,
                    active=enabled)
