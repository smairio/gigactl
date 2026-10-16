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

# Order is the swatch order in the UI: an 8-wide grid read like a book. The
# first two rows walk the colour wheel once, in order — red, orange, yellow,
# green, cyan, blue, purple, pink. The third row is the soft tints and whites,
# also in wheel order: warm pinks and peaches into the whites, then the cool
# ice and lavender. (Test-enforced: see test_lighting.py.) The values are tuned
# for the keyboard's LEDs, not a screen — "Warm white", for one, is mostly red
# because the LEDs' blue is the strongest of the three.
VIVID: tuple[tuple[str, RGB], ...] = (
    ("Red", (255, 0, 0)),
    ("Orange", (255, 80, 0)),
    ("Amber", (255, 130, 0)),
    ("Yellow", (255, 180, 0)),
    ("Lime", (140, 255, 0)),
    ("Green", (0, 255, 0)),
    ("Mint", (0, 255, 120)),
    ("Teal", (0, 210, 170)),
    ("Cyan", (0, 255, 255)),
    ("Aqua", (0, 200, 255)),
    ("Sky", (0, 140, 255)),
    ("Blue", (0, 0, 255)),
    ("Indigo", (70, 0, 255)),
    ("Purple", (160, 0, 255)),
    ("Magenta", (255, 0, 255)),
    ("Pink", (255, 0, 120)),
)
SOFT: tuple[tuple[str, RGB], ...] = (
    ("Rose", (255, 30, 60)),
    ("Coral", (255, 70, 50)),
    ("Peach", (255, 120, 70)),
    ("Warm white", (255, 170, 90)),
    ("Soft white", (255, 215, 170)),
    ("White", (255, 255, 255)),
    ("Ice", (120, 200, 255)),
    ("Lavender", (170, 110, 255)),
)
SWATCHES: tuple[tuple[str, RGB], ...] = VIVID + SOFT
GRID_COLUMNS = 8


def name_for(rgb: RGB) -> str | None:
    """The swatch name for this exact colour, or None when it is a custom one
    (nothing highlights, and the custom-colour button carries it instead)."""
    target = tuple(int(c) for c in rgb)
    for name, value in SWATCHES:
        if value == target:
            return name
    return None
