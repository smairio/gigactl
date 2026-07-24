"""The keyboard colour swatches.

Names mirror the ``gkbd`` CLI (the reference implementation of every hardware
capability, PRODUCT.md). Each swatch is drawn in the *same* RGB it sends to the
backlight, so what you click is literally what the keyboard does — the honest
depiction DESIGN.md asks the keyboard preview for.

One deliberate deviation from ``gkbd``: blue is pure ``(0, 0, 255)`` rather than
``gkbd``'s dimmed ``(0, 0, 200)``. Intensity is the brightness control's job
here, and pure blue is what the daemon reports as its firmware default — so a
fresh machine lights up the Blue swatch instead of matching nothing.
"""
from __future__ import annotations

RGB = tuple[int, int, int]

# Order is the swatch order in the UI.
SWATCHES: tuple[tuple[str, RGB], ...] = (
    ("Blue", (0, 0, 255)),
    ("Red", (255, 0, 0)),
    ("Green", (0, 255, 0)),
    ("Yellow", (255, 180, 0)),
    ("Orange", (255, 80, 0)),
    ("Cyan", (0, 255, 255)),
    ("Purple", (160, 0, 255)),
    ("Pink", (255, 0, 120)),
    ("White", (255, 255, 255)),
)


def name_for(rgb: RGB) -> str | None:
    """The swatch name for this exact colour, or None when it is a custom one
    (nothing highlights, and the custom-colour button carries it instead)."""
    target = tuple(int(c) for c in rgb)
    for name, value in SWATCHES:
        if value == target:
            return name
    return None


def hex_of(rgb: RGB) -> str:
    r, g, b = (int(c) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"
