"""Live keyboard preview — the one place a coloured glow is correct.

DESIGN.md: "The keyboard preview glows in the real selected color — a literal
depiction of the physical backlight." So it is literal: the keys carry the exact
RGB the daemon was given, dimmed by the real brightness, and dark when the
backlight is off.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

_WIDTH = 268
_HEIGHT = 116
_COLS = 14
_ROWS = 4
_PAD = 14
_GAP = 3.0


def intensity(enabled: bool, brightness_pct: int) -> float:
    """0.0–1.0 glow strength. Off means dark, and so does 0 % — no flattering
    floor, because the point of the preview is to match the hardware."""
    if not enabled:
        return 0.0
    return max(0, min(100, int(brightness_pct))) / 100


def _rounded(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    import math
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class KeyboardPreview(Adw.Bin):
    __gtype_name__ = "GigactlKeyboardPreview"

    def __init__(self) -> None:
        super().__init__()
        self._rgb = (0, 0, 255)
        self._glow = 1.0

        area = Gtk.DrawingArea()
        area.set_content_width(_WIDTH)
        area.set_content_height(_HEIGHT)
        area.set_draw_func(self._draw)
        self._area = area
        self.set_child(area)

        self.set_accessible_role(Gtk.AccessibleRole.IMG)
        self.update((0, 0, 255), enabled=True, brightness_pct=100)

    def update(self, rgb: tuple[int, int, int], *, enabled: bool,
               brightness_pct: int) -> None:
        self._rgb = tuple(max(0, min(255, int(c))) for c in rgb)
        self._glow = intensity(enabled, brightness_pct)
        state = "off" if self._glow == 0 else f"{int(self._glow * 100)}%"
        self.set_tooltip_text(f"Keyboard backlight preview — {state}")
        self._area.queue_draw()

    def _draw(self, _area, cr, width, height, *_data) -> None:
        r, g, b = (c / 255 for c in self._rgb)
        glow = self._glow
        dark = Adw.StyleManager.get_default().get_dark()

        # the keyboard plate
        plate = 0.16 if not dark else 0.10
        cr.set_source_rgb(plate, plate, plate + 0.02)
        _rounded(cr, 1, 1, width - 2, height - 2, 12)
        cr.fill()

        # the glow spilling out from under the keys
        if glow > 0:
            cr.save()
            _rounded(cr, 1, 1, width - 2, height - 2, 12)
            cr.clip()
            cr.set_source_rgba(r, g, b, 0.30 * glow)
            cr.paint()
            cr.restore()

        # the keys themselves, lit in the real colour
        usable_w = width - 2 * _PAD
        usable_h = height - 2 * _PAD
        kw = (usable_w - _GAP * (_COLS - 1)) / _COLS
        kh = (usable_h - _GAP * (_ROWS - 1)) / _ROWS
        for row in range(_ROWS):
            for col in range(_COLS):
                x = _PAD + col * (kw + _GAP)
                y = _PAD + row * (kh + _GAP)
                # unlit keycap, then the backlight on top
                cr.set_source_rgb(0.22 if not dark else 0.17,
                                  0.22 if not dark else 0.17,
                                  0.24 if not dark else 0.19)
                _rounded(cr, x, y, kw, kh, 2.5)
                cr.fill()
                if glow > 0:
                    cr.set_source_rgba(r, g, b, 0.25 + 0.75 * glow)
                    _rounded(cr, x, y, kw, kh, 2.5)
                    cr.fill()
