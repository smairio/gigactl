"""Screen two: the custom fan curve.

Reached from ``Custom…`` on the Overview and returned from with the header's back
button. Edits apply live (debounced) through ``SetCurve``; the annotation reads
the machine's real temperature against the shape being drawn, and the 30 % floor
is stated rather than left as a surprise. A Firmware-auto escape stays visible
here, because PRODUCT.md #2 says every control screen must have one.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import curves, temperature  # noqa: E402
from .client import DaemonClient, Telemetry  # noqa: E402
from .curve_view import CurveView  # noqa: E402

# Long enough to coalesce a drag, short enough that the fans respond while the
# finger is still down.
_DEBOUNCE_MS = 220

CPU = "cpu"
GPU = "gpu"


class CurvePage(Adw.NavigationPage):
    __gtype_name__ = "GigactlCurvePage"

    def __init__(self, client: DaemonClient) -> None:
        super().__init__(title="Custom fan curve", tag="curve")
        self._client = client
        self._linked = True
        self._editing = CPU
        self._curves: dict[str, curves.Curve] = {
            CPU: list(curves.DEFAULT_SEED), GPU: list(curves.DEFAULT_SEED)}
        self._temps: dict[str, int | None] = {CPU: None, GPU: None}
        self._syncing = False
        # Whether there is a shape worth sending: the user has edited one, or the
        # daemon already runs a custom curve. Without this, merely toggling
        # Linked/Split would push the invented default seed and take the machine
        # off firmware — a view-mode switch must not be an edit.
        self._dirty = False
        self._daemon_has_curve = False
        self._send_source = 0
        self._pending: tuple[str, curves.Curve, bool] | None = None
        self._controls: list[Gtk.Widget] = []

        self._view = CurveView(on_changed=self._on_curve_edited)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.append(self._build_header())
        content.append(self._build_graph())
        content.append(self._build_footer())

        clamp = Adw.Clamp(maximum_size=820, child=content)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      child=clamp, vexpand=True)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(scroller)
        self.set_child(toolbar)

        # Initial toggle state is set only now: activating a toggle fires its
        # handler, which reads widgets the later builders create.
        self._syncing = True
        try:
            self._link_buttons[True].set_active(True)
            self._fan_buttons[CPU].set_active(True)
        finally:
            self._syncing = False
        self._refresh_view()

    # --- construction --------------------------------------------------------
    def _build_header(self) -> Gtk.Box:
        title = Gtk.Label(label="Custom fan curve", xalign=0.0, hexpand=True)
        title.add_css_class("sec")

        self._link_buttons: dict[bool, Gtk.ToggleButton] = {}
        segmented = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        segmented.add_css_class("linked")
        group: Gtk.ToggleButton | None = None
        for linked, label in ((True, "Linked"), (False, "Split CPU / GPU")):
            button = Gtk.ToggleButton(label=label)
            button.add_css_class("profile-btn")
            if group is None:
                group = button
            else:
                button.set_group(group)
            button.connect("toggled", self._on_link_toggled, linked)
            segmented.append(button)
            self._link_buttons[linked] = button
            self._controls.append(button)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.append(title)
        box.append(segmented)
        return box

    def _build_graph(self) -> Gtk.Box:
        eyebrow = Gtk.Label(label="TEMPERATURE → FAN SPEED", xalign=0.0, hexpand=True)
        eyebrow.add_css_class("eyebrow")
        hint = Gtk.Label(label="drag points to edit", xalign=1.0)
        hint.add_css_class("metric-sub")
        caption = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        caption.append(eyebrow)
        caption.append(hint)

        # Which fan the graph edits — only meaningful once the curves are split.
        self._fan_buttons: dict[str, Gtk.ToggleButton] = {}
        self._fan_switcher = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._fan_switcher.add_css_class("linked")
        self._fan_switcher.set_halign(Gtk.Align.START)
        group: Gtk.ToggleButton | None = None
        for fan, label in ((CPU, "CPU fan"), (GPU, "GPU fan")):
            button = Gtk.ToggleButton(label=label)
            button.add_css_class("profile-btn")
            if group is None:
                group = button
            else:
                button.set_group(group)
            button.connect("toggled", self._on_fan_toggled, fan)
            self._fan_switcher.append(button)
            self._fan_buttons[fan] = button
            self._controls.append(button)
        self._fan_switcher.set_visible(False)  # linked by default

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("curvebox")
        box.append(caption)
        box.append(self._fan_switcher)
        box.append(self._view)
        return box

    def _build_footer(self) -> Gtk.Box:
        self._annotation = Gtk.Label(halign=Gtk.Align.START)
        self._annotation.add_css_class("status-pill")

        note = Gtk.Label(
            label=f"Never below {curves.FLOOR_PCT}% above "
                  f"{curves.FLOOR_TEMP_C}° · changes apply straight away",
            xalign=0.0, hexpand=True, wrap=True)
        note.add_css_class("metric-sub")

        escape = Gtk.Button(label="Firmware auto")
        escape.add_css_class("profile-btn")
        escape.add_css_class("firmware")
        escape.set_tooltip_text("Hand the fans back to the laptop's own control")
        escape.connect("clicked", self._on_firmware_clicked)
        self._controls.append(escape)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.append(self._annotation)
        box.append(note)
        box.append(escape)
        return box

    # --- handlers ------------------------------------------------------------
    def _on_link_toggled(self, button: Gtk.ToggleButton, linked: bool) -> None:
        if not button.get_active():
            return
        self._fan_switcher.set_visible(not linked)
        if self._syncing:
            return
        self._linked = linked
        if linked:
            # linking adopts the curve on screen for both fans, which is what
            # the daemon does with a linked SetCurve
            self._curves[GPU] = list(self._curves[self._editing])
            self._curves[CPU] = list(self._curves[self._editing])
        self._refresh_view()
        if self._dirty or self._daemon_has_curve:
            self._queue_send(self._editing, self._curves[self._editing])

    def _on_fan_toggled(self, button: Gtk.ToggleButton, fan: str) -> None:
        if not button.get_active():
            return
        self._editing = fan
        self._refresh_view()

    def _on_curve_edited(self, points: curves.Curve) -> None:
        self._dirty = True
        self._curves[self._editing] = points
        if self._linked:
            self._curves[self._other()] = list(points)
        self._update_annotation()
        self._queue_send(self._editing, points)

    def _on_firmware_clicked(self, _button) -> None:
        self._client.set_profile("firmware")

    # --- sending -------------------------------------------------------------
    def _queue_send(self, which: str, points: curves.Curve) -> None:
        """Debounced, but the *value* is captured now: a daemon echo landing
        before the timer fires must not change what we send."""
        self._pending = (which, list(points), self._linked)
        if self._send_source:
            GLib.source_remove(self._send_source)
        self._send_source = GLib.timeout_add(_DEBOUNCE_MS, self._send_now)

    def _send_now(self) -> bool:
        self._send_source = 0
        pending, self._pending = self._pending, None
        if pending:
            which, points, linked = pending
            self._client.set_curve(which, points, linked)
        return GLib.SOURCE_REMOVE

    def cancel_pending(self) -> None:
        """Called when the window closes: the timer would otherwise fire into a
        torn-down page."""
        if self._send_source:
            GLib.source_remove(self._send_source)
        self._send_source = 0
        self._pending = None

    # --- daemon state --------------------------------------------------------
    def sync_from_daemon(self) -> None:
        # An edit of ours has not landed yet: adopting the daemon's older shape
        # now would fight the drag that is still in flight.
        if self._send_source:
            return
        snapshot = self._client.curves()
        if snapshot is None:
            return
        self._syncing = True
        try:
            self._linked = snapshot.linked
            self._daemon_has_curve = bool(snapshot.cpu)
            if snapshot.cpu:
                self._curves[CPU] = curves.seeded_from(snapshot.cpu)
                self._curves[GPU] = curves.seeded_from(
                    snapshot.cpu if snapshot.linked else snapshot.gpu)
            # else: firmware/manual drive nothing, so keep the shape on screen —
            # hitting "Firmware auto" must not erase what the user just drew.
            self._link_buttons[snapshot.linked].set_active(True)
            self._fan_switcher.set_visible(not snapshot.linked)
        finally:
            self._syncing = False
        self._refresh_view()

    def on_telemetry(self, t: Telemetry) -> None:
        self._temps[CPU] = t.cpu_temp
        self._temps[GPU] = t.gpu_temp
        self._push_now()
        self._update_annotation()

    def set_controls_sensitive(self, usable: bool) -> None:
        for widget in self._controls:
            widget.set_sensitive(usable)
        self._view.set_sensitive(usable)

    # --- view helpers --------------------------------------------------------
    def _refresh_view(self) -> None:
        self._view.set_points(self._curves[self._editing])
        self._view.set_ghost(None if self._linked else self._curves[self._other()])
        self._push_now()
        self._update_annotation()

    def _other(self) -> str:
        return GPU if self._editing == CPU else CPU

    def _push_now(self) -> None:
        """The daemon floors a fan on the hotter of its own and the CPU's
        temperature, so the marker needs both to predict honestly."""
        self._view.set_now(self._temps[self._editing], self._temps[CPU])

    def _update_annotation(self) -> None:
        temp = self._temps[self._editing]
        label = "CPU" if self._editing == CPU else "GPU"
        for band in temperature.Band:  # band.value == the css class
            self._annotation.remove_css_class(band.value)
        if temp is None:
            self._annotation.set_text(f"Waiting for the {label} temperature…")
            return
        self._annotation.set_text(curves.annotation(
            label, temp, self._curves[self._editing], self._temps[CPU]))
        # tinted by the same one temperature scale as the Overview's pill
        self._annotation.add_css_class(temperature.band(temp).value)
