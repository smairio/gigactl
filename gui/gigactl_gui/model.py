"""Which Gigabyte model is this, and how far do we trust it?

Mirrors the guard the ``gfan``/``gkbd`` CLIs already apply (see those scripts):
the vendor must be GIGABYTE; the **G6 KF** is the one verified machine; other
G5/G6/G7 models are *expected* to work (same Clevo platform, untested); anything
else is unsupported. DMI lives in world-readable sysfs, so the unprivileged GUI
classifies the machine itself — no daemon round-trip needed.

Honesty about this distinction is a product principle (PRODUCT.md #3): the banner
must never claim "verified" for a machine we have not actually run on.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

VENDOR_PATH = "/sys/class/dmi/id/sys_vendor"
MODEL_PATH = "/sys/class/dmi/id/product_name"

VERIFIED_MODEL = "G6 KF"
_FAMILY_PREFIXES = ("G5", "G6", "G7")


class Support(enum.Enum):
    """How much we trust this machine — drives the banner colour."""
    VERIFIED = "verified"
    EXPECTED = "expected"
    UNSUPPORTED = "unsupported"


def classify(vendor: str, name: str) -> Support:
    # Prefix match, like the gfan/gkbd guard (GIGABYTE*|Gigabyte*): some boards
    # report the long "Gigabyte Technology Co., Ltd." vendor string.
    if not vendor.strip().upper().startswith("GIGABYTE"):
        return Support.UNSUPPORTED
    n = name.strip().upper()
    if n == VERIFIED_MODEL:
        return Support.VERIFIED
    if n.startswith(_FAMILY_PREFIXES):
        return Support.EXPECTED
    return Support.UNSUPPORTED


@dataclass(frozen=True)
class Model:
    vendor: str
    name: str
    support: Support

    @property
    def banner(self) -> str:
        """Short, honest one-liner for the verification banner (PRODUCT.md #5)."""
        if self.support is Support.VERIFIED:
            return f"Gigabyte {self.name} detected · fully verified"
        if self.support is Support.EXPECTED:
            return f"Gigabyte {self.name} — expected to work (verified on {VERIFIED_MODEL})"
        machine = f"{self.vendor} {self.name}".strip() or "This machine"
        return f"{machine} — not a supported Gigabyte G5/G6/G7"


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def detect(vendor_path: str = VENDOR_PATH, model_path: str = MODEL_PATH) -> Model:
    vendor = _read(vendor_path)
    name = _read(model_path)
    return Model(vendor=vendor, name=name, support=classify(vendor, name))
