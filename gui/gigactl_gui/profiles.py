"""The fan-profile row.

Ids match the daemon's own profile names (``gigactld.curve``). Only these five
are one-click selectable; ``custom`` is reached through the curve editor screen
(a later ticket) and ``manual`` is what a direct duty override leaves behind —
neither has a button, but both must still be *named* honestly in the "Now:"
label, which is what :func:`display_name` is for.
"""
from __future__ import annotations

from dataclasses import dataclass

FIRMWARE = "firmware"
CUSTOM = "custom"
MANUAL = "manual"


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    subtitle: str

    @property
    def is_firmware(self) -> bool:
        """Firmware is the yield-to-hardware state, styled neutral rather than
        accent (DESIGN.md) — it is a first-class state, never an error."""
        return self.id == FIRMWARE


SELECTABLE: tuple[Profile, ...] = (
    Profile("quiet", "Quiet", "low noise"),
    Profile("balanced", "Balanced", "recommended"),
    Profile("performance", "Performance", "cooler"),
    Profile("max", "Max", "full speed"),
    Profile(FIRMWARE, "Firmware", "automatic"),
)

_EXTRA_NAMES = {CUSTOM: "Custom", MANUAL: "Manual"}


def display_name(profile_id: str) -> str:
    for p in SELECTABLE:
        if p.id == profile_id:
            return p.label
    return _EXTRA_NAMES.get(profile_id, profile_id)
