"""The editable temperature→speed graph.

A Cairo plot with five points, editable by drag **or keyboard** — DESIGN.md
requires every control to be keyboard-reachable and screen-reader-labelled, and
this is the one screen whose only real affordance is a graph. Focus it and:

* ``←``/``→`` move the selected point's temperature, ``↑``/``↓`` its speed
  (hold Shift for bigger steps);
* ``Page Up``/``Page Down`` select the previous/next point, ``Home``/``End`` the
  first/last.

The accessible label always names the selected point and its values, so the shape
is legible without seeing it.

The view owns no policy: it reports a moved point through ``on_changed`` and lets
the page decide what to send. Axis ticks are drawn *inside* the plot using the
same coordinate mapping as the curve, so a label can never drift away from the
column it names.
"""
from __future__ import annotations

import math
from typing import Callable

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from . import curves, style, temperature  # noqa: E402

_HEIGHT = 240
_POINT_R = 6.0
_AXIS_H = 18.0          # strip along the bottom for the tick labels
_GRID_DUTIES = (25, 50, 75)
_AXIS_TICKS = (40, 55, 70, 85, 100)
_TICK_FONT = 10.5
_STEP = 1
_BIG_STEP = 5


class CurveView(Adw.Bin):
    __gtype_name__ = "GigactlCurveView"

    def __init__(self, on_changed: Callable[[curves.Curve], None]) -> None:
        super().__init__()
        self._on_changed = on_changed
        self._points: curves.Curve = list(curves.DEFAULT_SEED)
        self._ghost: curves.Curve | None = None
        self._now_temp: int | None = None
        self._now_cpu_temp: int | None = None
        self._dragging: int | None = None
        self._drag_origin: tuple[float, float] | None = None
        self._selected = 0

        area = Gtk.DrawingArea(vexpand=True)
        area.add_css_class("curve-accent")  # style.accent_rgb reads this
        area.set_content_height(_HEIGHT)
        area.set_draw_func(self._draw)
        area.set_focusable(True)  # keyboard editing needs focus
        self._area = area
        self.set_child(area)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        area.add_controller(drag)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        area.add_controller(motion)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        area.add_controller(keys)

        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_: self._area.queue_draw())
        focus.connect("leave", lambda *_: self._area.queue_draw())
        area.add_controller(focus)

        area.set_accessible_role(Gtk.AccessibleRole.GROUP)
        self._describe()

    # --- state ---------------------------------------------------------------
    @property
    def points(self) -> curves.Curve:
        return list(self._points)

    def set_points(self, points: curves.Curve) -> None:
        """Load a shape from the daemon. Ignored mid-drag so the daemon's echo
        cannot fight the finger that is moving a point."""
        if self._dragging is not None:
            return
        self._points = list(points)
        self._selected = min(self._selected, len(self._points) - 1)
        self._describe()
        self._area.queue_draw()

    def set_ghost(self, points: curves.Curve | None) -> None:
        """The other fan's curve, drawn faintly in split mode so it is obvious
        the two are independent rather than one having vanished."""
        self._ghost = list(points) if points else None
        self._area.queue_draw()

    def set_now(self, temp: int | None, cpu_temp: int | None = None) -> None:
        """Live telemetry for the marker. ``cpu_temp`` lets the predicted duty
        account for the daemon's floor engaging on the hotter of the two."""
        self._now_temp = temp
        self._now_cpu_temp = cpu_temp
        self._area.queue_draw()

    # --- editing -------------------------------------------------------------
    def _plot_size(self) -> tuple[float, float]:
        """The curve's own area, excluding the tick strip along the bottom."""
        return (float(self._area.get_width()),
                max(0.0, float(self._area.get_height()) - _AXIS_H))

    def _commit(self, points: curves.Curve) -> None:
        if points == self._points:
            return
        self._points = points
        self._describe()
        self._area.queue_draw()
        self._on_changed(self.points)

    def _on_drag_begin(self, _gesture, x: float, y: float) -> None:
        width, height = self._plot_size()
        hit = curves.hit_test(self._points, x, y, width, height)
        self._dragging = hit
        self._drag_origin = (x, y)
        if hit is not None:
            self._selected = hit
            self._area.grab_focus()
            self._describe()
            self._area.queue_draw()

    def _on_drag_update(self, _gesture, dx: float, dy: float) -> None:
        if self._dragging is None or self._drag_origin is None:
            return
        width, height = self._plot_size()
        x0, y0 = self._drag_origin
        temp, duty = curves.from_pixel(x0 + dx, y0 + dy, width, height)
        self._commit(curves.move_point(self._points, self._dragging, temp, duty))

    def _on_drag_end(self, _gesture, _dx: float, _dy: float) -> None:
        self._dragging = None
        self._drag_origin = None

    def _on_motion(self, _controller, x: float, y: float) -> None:
        width, height = self._plot_size()
        over = curves.hit_test(self._points, x, y, width, height) is not None
        self._area.set_cursor(
            Gdk.Cursor.new_from_name("grab" if over else "default", None))

    def _on_key(self, _controller, keyval: int, _keycode: int,
                state: Gdk.ModifierType) -> bool:
        step = _BIG_STEP if state & Gdk.ModifierType.SHIFT_MASK else _STEP
        temp, duty = self._points[self._selected]
        last = len(self._points) - 1

        if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left):
            self._commit(curves.move_point(self._points, self._selected,
                                           temp - step, duty))
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right):
            self._commit(curves.move_point(self._points, self._selected,
                                           temp + step, duty))
        elif keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            self._commit(curves.move_point(self._points, self._selected,
                                           temp, duty + step))
        elif keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            self._commit(curves.move_point(self._points, self._selected,
                                           temp, duty - step))
        elif keyval in (Gdk.KEY_Page_Down, Gdk.KEY_KP_Page_Down):
            self._select(min(self._selected + 1, last))
        elif keyval in (Gdk.KEY_Page_Up, Gdk.KEY_KP_Page_Up):
            self._select(max(self._selected - 1, 0))
        elif keyval in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
            self._select(0)
        elif keyval in (Gdk.KEY_End, Gdk.KEY_KP_End):
            self._select(last)
        else:
            return False  # let Tab and everything else through
        return True

    def _select(self, index: int) -> None:
        self._selected = index
        self._describe()
        self._area.queue_draw()

    def _describe(self) -> None:
        """Keep the spoken description in step with the selection: colour and
        position are never the only signal (DESIGN.md a11y)."""
        temp, duty = self._points[self._selected]
        text = (f"Fan curve, point {self._selected + 1} of {len(self._points)}: "
                f"{temp} degrees, {duty} percent. "
                f"Arrow keys change it, Page Up and Page Down pick a point.")
        self._area.update_property([Gtk.AccessibleProperty.LABEL], [text])
        self._area.set_tooltip_text(
            f"Point {self._selected + 1}: {temp}° → {duty}%  "
            f"(drag, or use the arrow keys)")

    # --- drawing -------------------------------------------------------------
    def _draw(self, _area, cr, width, height, *_data) -> None:
        dark = style.is_dark()
        ar, ag, ab = style.accent_rgb(self._area)
        plot_h = max(1.0, height - _AXIS_H)

        # grid
        cr.set_line_width(1)
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.22)
        for duty in _GRID_DUTIES:
            _, y = curves.to_pixel(curves.TEMP_MIN, duty, width, plot_h)
            cr.move_to(0, y)
            cr.line_to(width, y)
        cr.stroke()

        # axis ticks, positioned by the same mapping as the curve
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.75)
        cr.select_font_face("", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(_TICK_FONT)
        for temp in _AXIS_TICKS:
            x, _ = curves.to_pixel(temp, 0, width, plot_h)
            label = f"{temp}°"
            extents = cr.text_extents(label)
            # nudge the end labels inside the plot so they are not clipped
            tx = min(max(x - extents.width / 2, 0), width - extents.width)
            cr.move_to(tx, height - 4)
            cr.show_text(label)

        # the other fan's shape, behind everything
        if self._ghost:
            cr.set_source_rgba(ar, ag, ab, 0.30)
            cr.set_line_width(1.5)
            cr.set_dash([4.0, 3.0])
            self._path(cr, self._ghost, width, plot_h)
            cr.stroke()
            cr.set_dash([])

        pixels = [curves.to_pixel(t, d, width, plot_h) for t, d in self._points]

        # filled area under the curve
        cr.move_to(pixels[0][0], plot_h)
        for px, py in pixels:
            cr.line_to(px, py)
        cr.line_to(pixels[-1][0], plot_h)
        cr.close_path()
        cr.set_source_rgba(ar, ag, ab, 0.20)
        cr.fill()

        # the curve itself
        cr.set_line_width(2.5)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_source_rgb(ar, ag, ab)
        self._path(cr, self._points, width, plot_h)
        cr.stroke()

        self._draw_now_marker(cr, width, plot_h, dark)
        self._draw_handles(cr, pixels, (ar, ag, ab), dark)

    @staticmethod
    def _path(cr, points: curves.Curve, width: float, height: float) -> None:
        pixels = [curves.to_pixel(t, d, width, height) for t, d in points]
        cr.move_to(*pixels[0])
        for px, py in pixels[1:]:
            cr.line_to(px, py)

    def _draw_now_marker(self, cr, width: float, plot_h: float, dark: bool) -> None:
        """Where the machine is sitting on this shape right now."""
        if self._now_temp is None:
            return
        clamped = max(curves.TEMP_MIN, min(curves.TEMP_MAX, self._now_temp))
        nx, _ = curves.to_pixel(clamped, 0, width, plot_h)
        duty = curves.predicted(self._points, self._now_temp, self._now_cpu_temp)
        _, ny = curves.to_pixel(curves.TEMP_MIN, duty, width, plot_h)

        r, g, b = temperature.color_rgb(self._now_temp, dark=dark)
        cr.set_source_rgba(r, g, b, 0.75)
        cr.set_line_width(1)
        cr.set_dash([3.0, 3.0])
        cr.move_to(nx, 0)
        cr.line_to(nx, plot_h)
        cr.stroke()
        cr.set_dash([])
        cr.arc(nx, ny, 5, 0, math.tau)
        cr.fill()

    def _draw_handles(self, cr, pixels, accent, dark: bool) -> None:
        ar, ag, ab = accent
        focused = self._area.has_focus()
        for i, (px, py) in enumerate(pixels):
            if dark:
                cr.set_source_rgb(0.13, 0.13, 0.14)
            else:
                cr.set_source_rgb(1, 1, 1)
            cr.arc(px, py, _POINT_R, 0, math.tau)
            cr.fill()
            cr.set_source_rgb(ar, ag, ab)
            cr.set_line_width(2.5)
            cr.arc(px, py, _POINT_R, 0, math.tau)
            cr.stroke()
            # the keyboard selection, visible only while the graph has focus
            if focused and i == self._selected:
                cr.set_source_rgba(ar, ag, ab, 0.9)
                cr.set_line_width(1.5)
                cr.arc(px, py, _POINT_R + 4, 0, math.tau)
                cr.stroke()
