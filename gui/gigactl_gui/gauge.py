"""Semantic temperature ring gauge (DESIGN.md hero).

A Cairo arc filled proportionally to the temperature and coloured by its band,
with the big tabular number overlaid at the centre and the sensor label at the
bottom — a literal "how hot, at a glance" dial. Colour and fill both come from
the one temperature scale in :mod:`temperature`.
"""
from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from . import temperature  # noqa: E402

_SIZE = 126
_ARC_START = math.radians(135)   # leave the arc open at the bottom
_ARC_SWEEP = math.radians(270)
_LINE = 12


def _hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


class TempGauge(Adw.Bin):
    __gtype_name__ = "GigactlTempGauge"

    def __init__(self, label: str) -> None:
        super().__init__()
        self._temp: float | None = None

        self._area = Gtk.DrawingArea()
        self._area.set_content_width(_SIZE)
        self._area.set_content_height(_SIZE)
        self._area.set_draw_func(self._draw)

        self._num = Gtk.Label(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self._num.add_css_class("gauge-num")

        self._name = Gtk.Label(label=label.upper(), halign=Gtk.Align.CENTER,
                               valign=Gtk.Align.END)
        self._name.add_css_class("gauge-label")
        self._name.set_margin_bottom(8)

        overlay = Gtk.Overlay()
        overlay.set_child(self._area)
        overlay.add_overlay(self._num)
        overlay.add_overlay(self._name)
        self.set_child(overlay)

        self.set_temp(None)

    def set_temp(self, temp_c: float | None) -> None:
        self._temp = temp_c
        if temp_c is None:
            self._num.set_markup('<span size="xx-large" weight="bold">–</span>')
        else:
            color = temperature.color_hex(temp_c, dark=self._dark())
            self._num.set_markup(
                f'<span size="xx-large" weight="bold" foreground="{color}">'
                f'{int(round(temp_c))}<span size="small">°</span></span>'
            )
        self._area.queue_draw()

    @staticmethod
    def _dark() -> bool:
        return Adw.StyleManager.get_default().get_dark()

    def _draw(self, _area, cr, width, height, *_data) -> None:
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - _LINE / 2 - 2
        cr.set_line_width(_LINE)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)

        # faint full track
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.18)
        cr.arc(cx, cy, radius, _ARC_START, _ARC_START + _ARC_SWEEP)
        cr.stroke()

        if self._temp is None:
            return
        r, g, b = _hex_to_rgb(temperature.color_hex(self._temp, dark=self._dark()))
        cr.set_source_rgb(r, g, b)
        cr.arc(cx, cy, radius, _ARC_START,
               _ARC_START + _ARC_SWEEP * temperature.gauge_fraction(self._temp))
        cr.stroke()
