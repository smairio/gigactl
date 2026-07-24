"""The editable temperature→speed graph.

A Cairo plot with five draggable points. It owns no policy: it reports a moved
point through ``on_changed`` and lets the page decide what to send. The "now"
marker is drawn from live telemetry so the shape is read against the machine's
actual temperature, which is the whole point of editing a curve.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from . import curves, temperature  # noqa: E402

_HEIGHT = 220
_POINT_R = 6.0
_GRID_DUTIES = (25, 50, 75)
_AXIS_TICKS = (40, 55, 70, 85, 100)


def _accent(widget: Gtk.Widget) -> tuple[float, float, float]:
    """The platform accent colour, so the plot belongs to the user's theme.

    Read from the widget's own CSS colour (the ``.curve-accent`` rule sets it to
    ``@accent_color``) rather than a style-context lookup, which GTK 4.10
    deprecated.
    """
    rgba = widget.get_color()
    return (rgba.red, rgba.green, rgba.blue)


class CurveView(Adw.Bin):
    __gtype_name__ = "GigactlCurveView"

    def __init__(self, on_changed: Callable[[curves.Curve], None]) -> None:
        super().__init__()
        self._on_changed = on_changed
        self._points: curves.Curve = list(curves.DEFAULT_SEED)
        self._ghost: curves.Curve | None = None
        self._now_temp: int | None = None
        self._dragging: int | None = None
        self._drag_origin: tuple[float, float] | None = None

        area = Gtk.DrawingArea(vexpand=True)
        area.add_css_class("curve-accent")  # see _accent()
        area.set_content_height(_HEIGHT)
        area.set_draw_func(self._draw)
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

        self.set_accessible_role(Gtk.AccessibleRole.IMG)
        self.set_tooltip_text("Drag a point to change the curve")

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
        self._area.queue_draw()

    def set_ghost(self, points: curves.Curve | None) -> None:
        """The other fan's curve, drawn faintly in split mode so it is obvious
        the two are independent rather than one having vanished."""
        self._ghost = list(points) if points else None
        self._area.queue_draw()

    def set_now_temp(self, temp: int | None) -> None:
        self._now_temp = temp
        self._area.queue_draw()

    # --- dragging ------------------------------------------------------------
    def _size(self) -> tuple[float, float]:
        return (float(self._area.get_width()), float(self._area.get_height()))

    def _on_drag_begin(self, _gesture, x: float, y: float) -> None:
        width, height = self._size()
        self._dragging = curves.hit_test(self._points, x, y, width, height)
        self._drag_origin = (x, y)

    def _on_drag_update(self, _gesture, dx: float, dy: float) -> None:
        if self._dragging is None or self._drag_origin is None:
            return
        width, height = self._size()
        x0, y0 = self._drag_origin
        temp, duty = curves.from_pixel(x0 + dx, y0 + dy, width, height)
        moved = curves.move_point(self._points, self._dragging, temp, duty)
        if moved != self._points:
            self._points = moved
            self._area.queue_draw()
            self._on_changed(self.points)

    def _on_drag_end(self, _gesture, _dx: float, _dy: float) -> None:
        self._dragging = None
        self._drag_origin = None

    def _on_motion(self, _controller, x: float, y: float) -> None:
        width, height = self._size()
        over = curves.hit_test(self._points, x, y, width, height) is not None
        self._area.set_cursor(
            Gdk.Cursor.new_from_name("grab" if over else "default", None))

    # --- drawing -------------------------------------------------------------
    def _draw(self, _area, cr, width, height, *_data) -> None:
        dark = Adw.StyleManager.get_default().get_dark()
        ar, ag, ab = _accent(self._area)

        # grid
        cr.set_line_width(1)
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.22)
        for duty in _GRID_DUTIES:
            _, y = curves.to_pixel(curves.TEMP_MIN, duty, width, height)
            cr.move_to(0, y)
            cr.line_to(width, y)
        cr.stroke()

        # the other fan's shape, behind everything
        if self._ghost:
            cr.set_source_rgba(ar, ag, ab, 0.30)
            cr.set_line_width(1.5)
            cr.set_dash([4.0, 3.0])
            ghost = [curves.to_pixel(t, d, width, height) for t, d in self._ghost]
            cr.move_to(*ghost[0])
            for px, py in ghost[1:]:
                cr.line_to(px, py)
            cr.stroke()
            cr.set_dash([])

        pixels = [curves.to_pixel(t, d, width, height) for t, d in self._points]

        # filled area under the curve
        cr.move_to(pixels[0][0], height)
        for px, py in pixels:
            cr.line_to(px, py)
        cr.line_to(pixels[-1][0], height)
        cr.close_path()
        cr.set_source_rgba(ar, ag, ab, 0.20)
        cr.fill()

        # the curve itself
        cr.set_line_width(2.5)
        cr.set_line_join(1)  # cairo.LINE_JOIN_ROUND
        cr.set_source_rgb(ar, ag, ab)
        cr.move_to(*pixels[0])
        for px, py in pixels[1:]:
            cr.line_to(px, py)
        cr.stroke()

        # the "now" marker: where the machine is sitting on this shape
        if self._now_temp is not None:
            nx, _ = curves.to_pixel(
                max(curves.TEMP_MIN, min(curves.TEMP_MAX, self._now_temp)),
                0, width, height)
            _, ny = curves.to_pixel(
                curves.TEMP_MIN, curves.predicted(self._points, self._now_temp),
                width, height)
            hot = temperature.color_hex(self._now_temp, dark=dark).lstrip("#")
            hr, hg, hb = (int(hot[i:i + 2], 16) / 255 for i in (0, 2, 4))
            cr.set_source_rgba(hr, hg, hb, 0.75)
            cr.set_line_width(1)
            cr.set_dash([3.0, 3.0])
            cr.move_to(nx, 0)
            cr.line_to(nx, height)
            cr.stroke()
            cr.set_dash([])
            cr.arc(nx, ny, 5, 0, 6.283185307179586)
            cr.fill()

        # draggable handles, drawn last so they sit on top
        for px, py in pixels:
            cr.set_source_rgb(1, 1, 1) if not dark else cr.set_source_rgb(0.13, 0.13, 0.14)
            cr.arc(px, py, _POINT_R, 0, 6.283185307179586)
            cr.fill()
            cr.set_source_rgb(ar, ag, ab)
            cr.set_line_width(2.5)
            cr.arc(px, py, _POINT_R, 0, 6.283185307179586)
            cr.stroke()


def axis_labels() -> Gtk.Box:
    """The temperature ticks under the graph."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
    for i, temp in enumerate(_AXIS_TICKS):
        label = Gtk.Label(label=f"{temp}°")
        label.add_css_class("axis-tick")
        label.set_halign(Gtk.Align.START if i == 0
                         else Gtk.Align.END if i == len(_AXIS_TICKS) - 1
                         else Gtk.Align.CENTER)
        box.append(label)
    return box
