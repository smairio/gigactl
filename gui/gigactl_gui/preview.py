"""Live keyboard preview — the one place a coloured glow is correct.

DESIGN.md: "The keyboard preview glows in the real selected color — a literal
depiction of the physical backlight." So it is literal: the keys carry the exact
RGB the daemon was given, dimmed by the real brightness, dark when the backlight
is off, and *unlit with no colour at all* until the daemon has actually told us
what the backlight is doing (the hero shows "—" for the same reason).

Effects animate here too, computed from the wall clock with the daemon's own
formulas (``backlight.frame``) so the preview and the keys agree without the
daemon streaming frames. The animation runs on a frame-clock tick callback,
which GTK pauses by itself while the window is hidden; it is removed entirely
for still looks. Dark-because-idle shows dark, because that is the truth.
"""
from __future__ import annotations

import math
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import lighting, style  # noqa: E402
from .backlight import frame, intensity, is_animated  # noqa: E402

_WIDTH = 268   # minimum; the preview stretches to the row
_HEIGHT = 128
_COLS = 15
_ROWS = 5
_PAD = 14
_GAP = 3.0


def _rounded(cr, x: float, y: float, w: float, h: float, r: float) -> None:
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
        self._rgb = (0, 0, 0)
        self._glow = 0.0
        self._known = False  # nothing claimed until the daemon reports
        self._effect = lighting.STATIC
        self._period_s = 0
        self._dimmed = False
        self._cpu_temp: float | None = None
        self._tick_id = 0

        area = Gtk.DrawingArea(hexpand=True)
        area.set_content_width(_WIDTH)
        area.set_content_height(_HEIGHT)
        area.set_draw_func(self._draw)
        self._area = area
        self.set_child(area)

        self.set_accessible_role(Gtk.AccessibleRole.IMG)
        self.set_tooltip_text("Keyboard backlight preview — waiting for the daemon")

    def update(self, rgb: tuple[int, int, int], *, enabled: bool,
               brightness_pct: int, effect: str = lighting.STATIC,
               period_s: int = 0, dimmed: bool = False) -> None:
        self._rgb = tuple(max(0, min(255, int(c))) for c in rgb)
        self._glow = 0.0 if dimmed else intensity(enabled, brightness_pct)
        self._effect = effect
        self._period_s = period_s
        self._dimmed = dimmed
        self._known = True
        if dimmed:
            state = "dark until you type"
        elif self._glow == 0:
            state = "off"
        else:
            state = f"{int(self._glow * 100)}%"
        self.set_tooltip_text(f"Keyboard backlight preview — {state}")
        self._sync_animation()
        self._area.queue_draw()

    def set_cpu_temp(self, temp_c: float | None) -> None:
        """Feeds the CPU-heat effect. Smoothed like the daemon's, so the
        preview drifts the way the keys do instead of jumping."""
        if temp_c is None or self._cpu_temp is None:
            self._cpu_temp = temp_c
        else:
            self._cpu_temp += 0.35 * (temp_c - self._cpu_temp)
        if self._effect == lighting.TEMPERATURE:
            self._area.queue_draw()

    def _sync_animation(self) -> None:
        wanted = self._known and self._glow > 0 and is_animated(self._effect)
        if wanted and not self._tick_id:
            self._tick_id = self._area.add_tick_callback(self._on_tick)
        elif not wanted and self._tick_id:
            self._area.remove_tick_callback(self._tick_id)
            self._tick_id = 0

    def _on_tick(self, area, _clock) -> bool:
        area.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _draw(self, _area, cr, width, height, *_data) -> None:
        rgb, glow = frame(self._effect, self._period_s, self._rgb,
                          self._glow if self._known else 0.0, time.time(),
                          self._cpu_temp)
        r, g, b = (c / 255 for c in rgb)
        dark = style.is_dark()

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
