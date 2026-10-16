"""The rainbow square: pick any keyboard colour right on the main window.

Left to right walks the colour wheel (red, yellow, green, cyan, blue, magenta,
back to red); top to bottom fades from the full colour to white. There is no
dark half, unlike a paint program's picker, because darkness on a backlight is
the brightness slider's job — a "dark red" here would just be dim red, and two
controls for one thing is one too many.

Click or drag to choose; the arrow keys move the marker for keyboard users
(Left/Right change the colour, Up/Down how pale it is) and Enter or Space
applies it. ``on_pick(rgb, final)`` fires continuously while dragging
(``final=False``, for the live preview) and once more on release
(``final=True``).
"""
from __future__ import annotations

import colorsys
import math
from typing import Callable

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

RGB = tuple[int, int, int]

_RADIUS = 9.0
_HUE_STEPS = (0.0, 1 / 6, 2 / 6, 3 / 6, 4 / 6, 5 / 6, 1.0)


def rgb_at(x_frac: float, y_frac: float) -> RGB:
    """The colour under a point: x is the hue, y the paleness (0 = full)."""
    hue = min(max(x_frac, 0.0), 1.0)
    sat = 1.0 - min(max(y_frac, 0.0), 1.0)
    r, g, b = colorsys.hsv_to_rgb(hue % 1.0, sat, 1.0)
    return (round(r * 255), round(g * 255), round(b * 255))


def position_of(rgb: RGB) -> tuple[float, float]:
    """Where a colour sits on the square (fractions). A dim colour is placed
    by its hue and paleness alone; black and greys land on the white edge."""
    h, s, _v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    return h, 1.0 - s


class ColourSquare(Gtk.DrawingArea):
    __gtype_name__ = "GigactlColourSquare"

    def __init__(self, on_pick: Callable[[RGB, bool], None]) -> None:
        super().__init__()
        self._on_pick = on_pick
        self._pos: tuple[float, float] | None = None  # nothing marked yet
        self.set_content_width(220)
        self.set_content_height(150)
        self.set_hexpand(True)
        self.set_focusable(True)
        self.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        self.set_draw_func(self._draw)
        self.add_css_class("colour-square")
        self.set_accessible_role(Gtk.AccessibleRole.SLIDER)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Colour square"])
        self.set_tooltip_text("Click or drag to choose any colour")

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)
        self._drag_origin = (0.0, 0.0)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # --- state --------------------------------------------------------------
    def show_colour(self, rgb: RGB) -> None:
        """Move the marker to a colour chosen elsewhere (a preset, the daemon)."""
        self._pos = position_of(rgb)
        self.queue_draw()

    def clear(self) -> None:
        self._pos = None
        self.queue_draw()

    # --- input --------------------------------------------------------------
    def _pick_at(self, x: float, y: float, final: bool) -> None:
        w, h = max(1, self.get_width()), max(1, self.get_height())
        fx, fy = min(max(x / w, 0.0), 1.0), min(max(y / h, 0.0), 1.0)
        self._pos = (fx, fy)
        self.queue_draw()
        self._on_pick(rgb_at(fx, fy), final)

    def _on_drag_begin(self, _gesture, x, y) -> None:
        self.grab_focus()
        self._drag_origin = (x, y)
        self._pick_at(x, y, False)

    def _on_drag_update(self, _gesture, dx, dy) -> None:
        ox, oy = self._drag_origin
        self._pick_at(ox + dx, oy + dy, False)

    def _on_drag_end(self, _gesture, dx, dy) -> None:
        ox, oy = self._drag_origin
        self._pick_at(ox + dx, oy + dy, True)

    def _on_key(self, _ctl, keyval, _code, _state) -> bool:
        fx, fy = self._pos or (0.0, 0.0)
        step = {Gdk.KEY_Left: (-1 / 72, 0), Gdk.KEY_Right: (1 / 72, 0),
                Gdk.KEY_Up: (0, -0.05), Gdk.KEY_Down: (0, 0.05)}.get(keyval)
        if step:
            self._pos = ((fx + step[0]) % 1.0, min(max(fy + step[1], 0.0), 1.0))
            self.queue_draw()
            self._on_pick(rgb_at(*self._pos), False)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space) and self._pos:
            self._on_pick(rgb_at(*self._pos), True)
            return True
        return False

    # --- drawing ------------------------------------------------------------
    def _draw(self, _area, cr, width, height, *_data) -> None:
        r = 10.0
        cr.new_sub_path()
        cr.arc(width - r, r, r, -math.pi / 2, 0)
        cr.arc(width - r, height - r, r, 0, math.pi / 2)
        cr.arc(r, height - r, r, math.pi / 2, math.pi)
        cr.arc(r, r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.save()
        cr.clip()

        hues = cairo.LinearGradient(0, 0, width, 0)
        for stop in _HUE_STEPS:
            red, green, blue = colorsys.hsv_to_rgb(stop % 1.0, 1.0, 1.0)
            hues.add_color_stop_rgb(stop, red, green, blue)
        cr.set_source(hues)
        cr.paint()

        pale = cairo.LinearGradient(0, 0, 0, height)
        pale.add_color_stop_rgba(0, 1, 1, 1, 0)
        pale.add_color_stop_rgba(1, 1, 1, 1, 1)
        cr.set_source(pale)
        cr.paint()
        cr.restore()

        if self._pos is None:
            return
        x = self._pos[0] * width
        y = self._pos[1] * height
        red, green, blue = (c / 255 for c in rgb_at(*self._pos))
        cr.arc(x, y, _RADIUS, 0, math.tau)
        cr.set_source_rgb(red, green, blue)
        cr.fill_preserve()
        cr.set_line_width(3)
        cr.set_source_rgb(1, 1, 1)
        cr.stroke_preserve()
        cr.set_line_width(1)
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.stroke()
        if self.has_focus():
            cr.arc(x, y, _RADIUS + 4, 0, math.tau)
            cr.set_source_rgba(0, 0, 0, 0.35)
            cr.set_line_width(1.5)
            cr.stroke()
