"""The main window: the Overview (model banner, live hero, working controls).

Layout follows DESIGN.md, top-to-bottom, beginner-first: the verification
banner, the live-state hero, the fan-profile row, then the keyboard light. The
custom-curve screen is a later ticket, so ``Custom…`` has no button yet — but a
saved custom curve (or a manual override) is still named honestly in the "Now:"
label rather than silently showing nothing selected.

Two rules the wiring depends on:

* every control reflects the **daemon's** state, not its own — user actions are
  echoed locally for instant feedback, then corrected by the property change the
  daemon broadcasts, so a refused write never leaves a lying UI;
* while applying daemon state we set ``_syncing``, because programmatically
  moving a switch or slider fires the same signal a click does and would bounce
  straight back to the daemon.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import palette, profiles, temperature  # noqa: E402
from .client import DaemonClient, Telemetry  # noqa: E402
from .gauge import TempGauge  # noqa: E402
from .model import Model, Support  # noqa: E402
from .preview import KeyboardPreview  # noqa: E402

# A colour/brightness write goes all the way to the EC; coalesce slider drags
# instead of firing one call per pixel.
_BRIGHTNESS_DEBOUNCE_MS = 180


def _rpm(value: int) -> str:
    # Group thousands with a narrow no-break space (U+202F) so it reads "4 637"
    # and never wraps mid-value.
    return f"{value:,}".replace(",", " ")


def _section_title(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0.0)
    label.add_css_class("sec")
    return label


class OverviewWindow(Adw.ApplicationWindow):
    __gtype_name__ = "GigactlOverviewWindow"

    def __init__(self, app: Adw.Application, model: Model) -> None:
        super().__init__(application=app)
        self.set_title("GigaControl")
        self.set_default_size(760, 720)

        self._model = model
        self._syncing = False
        self._brightness_source = 0
        self._pending_brightness: int | None = None
        # Last profile the daemon told us about. Gio clears the proxy's property
        # cache when the daemon goes away, so without this a failed click would
        # leave the row showing a profile that never took effect.
        self._last_profile: str | None = None
        self._controls: list[Gtk.Widget] = []

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.append(self._build_banner(model))
        content.append(self._build_hero())
        content.append(Gtk.Separator())
        content.append(self._build_profiles())
        content.append(Gtk.Separator())
        content.append(self._build_keyboard())

        clamp = Adw.Clamp(maximum_size=820, child=content)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      child=clamp, vexpand=True)
        self._toasts = Adw.ToastOverlay(child=scroller)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(self._toasts)
        self.set_content(toolbar)

        # Live data + control results. Callbacks fire on the GLib main loop, so
        # they touch widgets directly.
        self._client = DaemonClient(on_telemetry=self._on_telemetry,
                                    on_availability=self._on_availability,
                                    on_state=self._sync_from_daemon,
                                    on_error=self._show_error)
        self._update_sensitivity()
        self._client.start()

    # --- construction --------------------------------------------------------
    def _build_banner(self, model: Model) -> Gtk.Box:
        icon = {
            Support.VERIFIED: "emblem-ok-symbolic",
            Support.EXPECTED: "dialog-warning-symbolic",
            Support.UNSUPPORTED: "dialog-error-symbolic",
        }[model.support]
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.add_css_class("verbar")
        box.add_css_class(model.support.value)  # "verified" / "expected" / "unsupported"
        box.append(Gtk.Image.new_from_icon_name(icon))
        text = model.banner
        if model.support is Support.UNSUPPORTED:
            text += " — controls are switched off to keep it safe."
        box.append(Gtk.Label(label=text, xalign=0.0, wrap=True))
        return box

    def _build_hero(self) -> Gtk.Box:
        self._cpu_gauge = TempGauge("CPU")
        self._gpu_gauge = TempGauge("GPU")
        gauges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        gauges.append(self._cpu_gauge)
        gauges.append(self._gpu_gauge)

        self._pill = Gtk.Label(halign=Gtk.Align.START)
        self._pill.add_css_class("status-pill")

        self._cpu_rpm, self._cpu_duty, cpu_block = self._build_fan_block("CPU fan")
        self._gpu_rpm, self._gpu_duty, gpu_block = self._build_fan_block("GPU fan")
        fans = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=28)
        fans.append(cpu_block)
        fans.append(gpu_block)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                       valign=Gtk.Align.CENTER, hexpand=True)
        side.append(self._pill)
        side.append(fans)

        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=28)
        hero.append(gauges)
        hero.append(side)

        self._show_waiting()
        return hero

    def _build_fan_block(self, label: str) -> tuple[Gtk.Label, Gtk.Label, Gtk.Box]:
        eyebrow = Gtk.Label(label=label.upper(), xalign=0.0)
        eyebrow.add_css_class("eyebrow")

        rpm = Gtk.Label(xalign=0.0)
        rpm.add_css_class("rpm-value")
        unit = Gtk.Label(label="rpm", xalign=0.0)
        unit.add_css_class("metric-sub")
        rpm_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        rpm_line.set_valign(Gtk.Align.BASELINE)
        rpm_line.append(rpm)
        rpm_line.append(unit)

        duty = Gtk.Label(xalign=0.0)
        duty.add_css_class("metric-sub")

        block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        block.append(eyebrow)
        block.append(rpm_line)
        block.append(duty)
        return rpm, duty, block

    def _build_profiles(self) -> Gtk.Box:
        self._now_label = Gtk.Label(xalign=1.0, hexpand=True,
                                    halign=Gtk.Align.END)
        self._now_label.add_css_class("metric-sub")
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.append(_section_title("Fan profile"))
        header.append(self._now_label)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        row.add_css_class("linked")
        self._profile_buttons: dict[str, Gtk.ToggleButton] = {}
        group: Gtk.ToggleButton | None = None
        for profile in profiles.SELECTABLE:
            button = Gtk.ToggleButton()
            button.add_css_class("profile-btn")
            if profile.is_firmware:
                # the yield-to-hardware state: neutral fill, never accent
                button.add_css_class("firmware")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            name = Gtk.Label(label=profile.label)
            name.add_css_class("profile-name")
            subtitle = Gtk.Label(label=profile.subtitle)
            subtitle.add_css_class("profile-sub")
            inner.append(name)
            inner.append(subtitle)
            button.set_child(inner)
            button.set_tooltip_text(f"{profile.label} — {profile.subtitle}")
            if group is None:
                group = button
            else:
                button.set_group(group)
            button.connect("toggled", self._on_profile_toggled, profile.id)
            row.append(button)
            self._profile_buttons[profile.id] = button
            self._controls.append(button)

        escape = Gtk.Label(
            label="Firmware hands the fans back to the laptop — always one click away.",
            xalign=0.0, wrap=True)
        escape.add_css_class("metric-sub")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(header)
        box.append(row)
        box.append(escape)
        return box

    def _build_keyboard(self) -> Gtk.Box:
        self._switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self._switch.set_tooltip_text("Turn the keyboard backlight on or off")
        self._switch.connect("notify::active", self._on_switch_toggled)
        self._controls.append(self._switch)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.append(_section_title("Keyboard light"))
        spacer = Gtk.Box(hexpand=True)
        header.append(spacer)
        header.append(self._switch)

        swatch_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._swatches: list[tuple[Gtk.Button, palette.RGB]] = []
        for name, rgb in palette.SWATCHES:
            button = self._build_swatch(name, rgb)
            swatch_row.append(button)
            self._swatches.append((button, rgb))
            self._controls.append(button)

        self._color_button = Gtk.ColorDialogButton(
            dialog=Gtk.ColorDialog(title="Keyboard colour", with_alpha=False),
            valign=Gtk.Align.CENTER)
        self._color_button.set_tooltip_text("Pick any colour")
        self._color_button.connect("notify::rgba", self._on_custom_colour)
        self._controls.append(self._color_button)
        swatch_row.append(self._color_button)

        brightness_label = Gtk.Label(label="BRIGHTNESS", xalign=0.0)
        brightness_label.add_css_class("eyebrow")
        self._brightness = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self._brightness.set_draw_value(True)
        self._brightness.set_value_pos(Gtk.PositionType.RIGHT)
        self._brightness.set_hexpand(True)
        self._brightness.set_value(100)
        self._brightness.connect("value-changed", self._on_brightness_changed)
        self._controls.append(self._brightness)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       hexpand=True, valign=Gtk.Align.CENTER)
        left.append(swatch_row)
        left.append(brightness_label)
        left.append(self._brightness)

        self._preview = KeyboardPreview()

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        body.append(left)
        body.append(self._preview)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(header)
        box.append(body)
        return box

    def _build_swatch(self, name: str, rgb: palette.RGB) -> Gtk.Button:
        """A button that paints itself in the exact colour it will send, so the
        swatch is a truthful sample rather than a themed approximation."""
        area = Gtk.DrawingArea()
        area.set_content_width(26)
        area.set_content_height(26)
        area.set_draw_func(self._draw_swatch, rgb)

        button = Gtk.Button(child=area)
        button.add_css_class("swatch")
        button.set_tooltip_text(name)
        # colour is never the only signal (DESIGN.md a11y): the name is the
        # accessible label and the tooltip.
        button.update_property([Gtk.AccessibleProperty.LABEL], [name])
        button.connect("clicked", self._on_swatch_clicked, rgb)
        return button

    @staticmethod
    def _draw_swatch(_area, cr, width, height, rgb) -> None:
        r, g, b = (c / 255 for c in rgb)
        cr.set_source_rgb(r, g, b)
        cr.arc(width / 2, height / 2, min(width, height) / 2 - 1, 0, 6.2832)
        cr.fill()

    # --- control handlers ----------------------------------------------------
    def _on_profile_toggled(self, button: Gtk.ToggleButton, profile_id: str) -> None:
        if self._syncing or not button.get_active():
            return
        self._client.set_profile(profile_id)

    def _on_switch_toggled(self, switch: Gtk.Switch, _param) -> None:
        if self._syncing:
            return
        self._client.set_keyboard_enabled(switch.get_active())
        self._echo_preview()

    def _on_swatch_clicked(self, _button, rgb: palette.RGB) -> None:
        if self._syncing:
            return
        self._apply_colour(rgb)

    def _on_custom_colour(self, button: Gtk.ColorDialogButton, _param) -> None:
        if self._syncing:
            return
        c = button.get_rgba()
        self._apply_colour((round(c.red * 255), round(c.green * 255),
                            round(c.blue * 255)))

    def _apply_colour(self, rgb: palette.RGB) -> None:
        self._client.set_keyboard_color(*rgb)
        # echo immediately; the daemon's property broadcast is what finally
        # decides, so a refused write self-corrects.
        self._select_swatch(rgb)
        self._echo_preview(rgb)

    def _on_brightness_changed(self, scale: Gtk.Scale) -> None:
        if self._syncing:
            return
        # Capture the value NOW, not when the timer fires: a property sync
        # arriving in between would otherwise rewrite the slider and we would
        # send the daemon's old value back to it.
        self._pending_brightness = int(scale.get_value())
        self._echo_preview()  # preview follows the drag instantly
        if self._brightness_source:
            GLib.source_remove(self._brightness_source)
        self._brightness_source = GLib.timeout_add(
            _BRIGHTNESS_DEBOUNCE_MS, self._send_brightness)

    def _send_brightness(self) -> bool:
        self._brightness_source = 0
        pending, self._pending_brightness = self._pending_brightness, None
        if pending is not None:
            self._client.set_keyboard_brightness(pending)
        return GLib.SOURCE_REMOVE

    # --- daemon state --------------------------------------------------------
    def _sync_from_daemon(self) -> None:
        """Pull the authoritative state back out of the daemon's properties."""
        self._syncing = True
        try:
            profile = self._client.active_profile() or self._last_profile
            if profile:
                self._last_profile = profile
                shown = GLib.markup_escape_text(profiles.display_name(profile))
                self._now_label.set_markup(f"Now: <b>{shown}</b>")
                target = self._profile_buttons.get(profile)
                if target is not None:
                    target.set_active(True)  # the group clears the others
                else:
                    # custom / manual: no button represents it, so show none
                    # selected rather than pretending one of these is active
                    for button in self._profile_buttons.values():
                        button.set_active(False)
            keyboard = self._client.keyboard_state()
            if keyboard:
                self._switch.set_active(keyboard.enabled)
                if self._brightness_source == 0:
                    # don't yank the slider out from under a drag that has not
                    # reached the daemon yet
                    self._brightness.set_value(keyboard.brightness_pct)
                self._select_swatch(keyboard.rgb)
                self._preview.update(keyboard.rgb, enabled=keyboard.enabled,
                                     brightness_pct=keyboard.brightness_pct)
        finally:
            self._syncing = False

    def _select_swatch(self, rgb: palette.RGB) -> None:
        match = palette.name_for(rgb)
        for button, value in self._swatches:
            if value == tuple(rgb) and match:
                button.add_css_class("selected")
            else:
                button.remove_css_class("selected")
        was_syncing = self._syncing
        self._syncing = True  # setting rgba re-enters _on_custom_colour
        try:
            colour = Gdk.RGBA()
            colour.red, colour.green, colour.blue = (c / 255 for c in rgb)
            colour.alpha = 1.0
            self._color_button.set_rgba(colour)
        finally:
            self._syncing = was_syncing

    def _echo_preview(self, rgb: palette.RGB | None = None) -> None:
        """Repaint the preview from what the widgets currently show."""
        if rgb is None:
            c = self._color_button.get_rgba()
            rgb = (round(c.red * 255), round(c.green * 255), round(c.blue * 255))
        self._preview.update(rgb, enabled=self._switch.get_active(),
                             brightness_pct=int(self._brightness.get_value()))

    # --- live updates --------------------------------------------------------
    def _on_telemetry(self, t: Telemetry) -> None:
        self._cpu_gauge.set_temp(t.cpu_temp)
        self._gpu_gauge.set_temp(t.gpu_temp)
        text, band = temperature.summary(t.cpu_temp, t.gpu_temp)
        self._set_pill(text, band.value)  # band.value == the css class
        self._cpu_rpm.set_text(_rpm(t.fan1_rpm))
        self._gpu_rpm.set_text(_rpm(t.fan2_rpm))
        self._cpu_duty.set_text(f"{t.fan1_duty_pct}% duty")
        self._gpu_duty.set_text(f"{t.fan2_duty_pct}% duty")

    def _on_availability(self, available: bool) -> None:
        if not available:
            self._show_waiting()
        self._update_sensitivity()

    def _update_sensitivity(self) -> None:
        """Controls need a daemon to talk to, and an unsupported machine must
        not be written to at all (DESIGN.md / PRODUCT.md safety invariants)."""
        supported = self._model.support is not Support.UNSUPPORTED
        usable = supported and self._client.available if hasattr(self, "_client") else False
        for widget in self._controls:
            widget.set_sensitive(usable)

    def _show_error(self, message: str) -> None:
        """A refused or rejected write surfaces inline, never silently — and the
        controls snap back, so a failed click never leaves a button claiming
        something the daemon did not do."""
        self._toasts.add_toast(Adw.Toast(title=message, timeout=6))
        # Deferred: correcting a grouped ToggleButton from inside its own
        # "toggled" emission does not stick, so resync once GTK is done.
        GLib.idle_add(self._resync)

    def _resync(self) -> bool:
        self._sync_from_daemon()
        return GLib.SOURCE_REMOVE

    def _show_waiting(self) -> None:
        self._cpu_gauge.set_temp(None)
        self._gpu_gauge.set_temp(None)
        self._set_pill("Waiting for the gigactl daemon…", None)
        for lbl in (self._cpu_rpm, self._gpu_rpm):
            lbl.set_text("—")
        for lbl in (self._cpu_duty, self._gpu_duty):
            lbl.set_text("")

    def _set_pill(self, text: str, band_css: str | None) -> None:
        self._pill.set_text(text)
        for b in temperature.Band:
            self._pill.remove_css_class(b.value)
        if band_css:
            self._pill.add_css_class(band_css)
