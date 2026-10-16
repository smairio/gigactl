"""The main window: the Overview, and the navigation host for screen two.

Layout follows DESIGN.md, top-to-bottom, beginner-first: the verification
banner, the live-state hero, the fan-profile row, then the keyboard light.
``Custom…`` opens the curve editor (:mod:`curve_page`) through an
``Adw.NavigationView``, which supplies the "back to overview" affordance. A
``manual`` override has no button of its own, so it is named in the "Now:" label
rather than leaving the row silently blank.

Two rules the wiring depends on:

* every control reflects the **daemon's** state, not its own — user actions are
  echoed locally for instant feedback, then corrected by the property change the
  daemon broadcasts, so a refused write never leaves a lying UI;
* while applying daemon state we set ``_syncing``, because programmatically
  moving a switch or slider fires the same signal a click does and would bounce
  straight back to the daemon.
"""
from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import lighting, palette, profiles, temperature  # noqa: E402
from .client import DaemonClient, Telemetry  # noqa: E402
from .colour_square import ColourSquare  # noqa: E402
from .curve_page import CurvePage  # noqa: E402
from .gauge import TempGauge  # noqa: E402
from .banner import banner as banner_for
from .model import Model, Support  # noqa: E402
from .preview import KeyboardPreview  # noqa: E402

# A colour/brightness write goes all the way to the EC; coalesce slider drags
# instead of firing one call per pixel.
_BRIGHTNESS_DEBOUNCE_MS = 180

# Default window width, and the height the title bar adds to the content's.
_WIDTH = 800
_HEADER_ALLOWANCE = 56
_PANEL_ALLOWANCE = 90


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
        self.set_default_size(_WIDTH, 860)

        self._model = model
        self._syncing = False
        self._brightness_source = 0
        self._pending_brightness: int | None = None
        # Colour picked on the square or typed as R/G/B: like brightness, sent
        # after a short pause so a drag is not a flood of EC writes.
        self._colour_source = 0
        self._pending_colour: palette.RGB | None = None
        self._shown_rgb: palette.RGB = (0, 0, 0)
        # Last profile the daemon told us about. Gio clears the proxy's property
        # cache when the daemon goes away, so without this a failed click would
        # leave the row showing a profile that never took effect.
        self._last_profile: str | None = None
        self._resync_source = 0
        self._controls: list[Gtk.Widget] = []

        # The client exists before the widgets because the curve page talks to
        # it; nothing reaches the bus until start() at the end.
        self._client = DaemonClient(on_telemetry=self._on_telemetry,
                                    on_availability=self._on_availability,
                                    on_state=self._sync_from_daemon,
                                    on_error=self.show_error)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
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
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(scroller)

        overview = Adw.NavigationPage(title="GigaControl", tag="overview",
                                      child=toolbar)
        self._curve_page = CurvePage(self._client)
        self._nav = Adw.NavigationView()
        self._nav.add(overview)
        self._nav.add(self._curve_page)

        # Toasts live above the whole stack so a refused write is visible on
        # whichever screen the user is looking at.
        self._toasts = Adw.ToastOverlay(child=self._nav)
        self.set_content(self._toasts)

        self._update_sensitivity()
        self.connect("close-request", self._on_close)
        self._fit_to_screen(clamp)
        self._client.start()

    def _fit_to_screen(self, content: Gtk.Widget) -> None:
        """Open tall enough to show every control without scrolling, but never
        taller than the screen (a 768-pixel laptop panel still gets a window
        that fits, and scrolls)."""
        _min, natural, _b, _b2 = content.measure(Gtk.Orientation.VERTICAL, _WIDTH)
        wanted = natural + _HEADER_ALLOWANCE
        display = Gdk.Display.get_default()
        tallest = 0
        if display is not None:
            monitors = display.get_monitors()
            for i in range(monitors.get_n_items()):
                tallest = max(tallest, monitors.get_item(i).get_geometry().height)
        if tallest:
            # leave room for the desktop's own panels (top bar, dock, taskbar)
            wanted = min(wanted, tallest - _PANEL_ALLOWANCE)
        self.set_default_size(_WIDTH, max(wanted, 560))

    def _on_close(self, _window) -> bool:
        """Drop pending timers: both would touch widgets after teardown."""
        for source in (self._brightness_source, self._resync_source,
                       self._colour_source):
            if source:
                GLib.source_remove(source)
        self._brightness_source = self._resync_source = self._colour_source = 0
        self._curve_page.cancel_pending()
        return False  # let the close proceed

    # --- construction --------------------------------------------------------
    def _build_banner(self, model: Model) -> Gtk.Box:
        """The banner box, kept so it can be re-stated without a restart: what
        it says depends on the EC, which can break and be fixed while the window
        is open."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.add_css_class("verbar")
        self._banner_box = box
        self._banner_icon = Gtk.Image()
        self._banner_label = Gtk.Label(xalign=0.0, wrap=True)
        box.append(self._banner_icon)
        box.append(self._banner_label)
        self._banner_tone = None
        self._update_banner()
        return box

    def _update_banner(self) -> None:
        state = banner_for(self._model, self._client.ec_status())
        if state.tone is not self._banner_tone:
            if self._banner_tone is not None:
                self._banner_box.remove_css_class(self._banner_tone.css_class)
            self._banner_box.add_css_class(state.tone.css_class)
            self._banner_tone = state.tone
        self._banner_icon.set_from_icon_name(state.icon)
        self._banner_label.set_label(state.text)

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

        # Custom... is a doorway, not a profile: it opens the curve editor
        # rather than calling SetProfile (selecting 'custom' with no curve set
        # would be refused by the daemon anyway).
        self._custom_button = Gtk.Button()
        self._custom_button.add_css_class("profile-btn")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        name = Gtk.Label(label="Custom…")
        name.add_css_class("profile-name")
        subtitle = Gtk.Label(label="edit curve")
        subtitle.add_css_class("profile-sub")
        inner.append(name)
        inner.append(subtitle)
        self._custom_button.set_child(inner)
        self._custom_button.set_tooltip_text("Draw your own temperature curve")
        self._custom_button.connect("clicked", self._on_custom_clicked)
        row.append(self._custom_button)
        self._controls.append(self._custom_button)

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
        """Everything the Windows Control Center offered, up front rather than
        two clicks deep: a live preview, the full colour grid, brightness, the
        effect, the auto-off timer and notification flashing."""
        self._switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self._switch.set_tooltip_text("Turn the keyboard backlight on or off")
        self._switch.connect("notify::active", self._on_switch_toggled)
        self._controls.append(self._switch)

        # Colour is never the only signal (DESIGN.md a11y): name it in words,
        # mirroring the profile row's "Now:".
        self._colour_label = Gtk.Label(xalign=1.0, hexpand=True,
                                       halign=Gtk.Align.END)
        self._colour_label.add_css_class("metric-sub")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.append(_section_title("Keyboard light"))
        header.append(self._colour_label)
        header.append(self._switch)

        # The preview, wide, like the keyboard picture in the Windows app.
        self._preview = KeyboardPreview()
        self._preview.set_hexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(header)
        box.append(self._preview)
        box.append(self._build_colour_grid())
        box.append(self._build_brightness())
        box.append(self._build_effects())
        box.append(self._build_light_settings())
        return box

    def _build_colour_grid(self) -> Gtk.Widget:
        """Colour, all on one screen: the rainbow square (any colour, by click
        or drag) with exact R/G/B boxes beneath it on the left, and the presets
        in colour-wheel order on the right. No dialog to open."""
        self._square = ColourSquare(self._on_square_pick)
        self._controls.append(self._square)

        self._rgb_spins: list[Gtk.SpinButton] = []
        spins = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                        halign=Gtk.Align.START)
        for letter, name in (("R", "Red"), ("G", "Green"), ("B", "Blue")):
            label = Gtk.Label(label=letter)
            label.add_css_class("metric-sub")
            spin = Gtk.SpinButton.new_with_range(0, 255, 1)
            spin.set_numeric(True)
            spin.set_width_chars(3)
            spin.update_property([Gtk.AccessibleProperty.LABEL], [f"{name} amount, 0 to 255"])
            spin.set_tooltip_text(f"{name}, 0–255")
            spin.connect("value-changed", self._on_rgb_spin)
            spins.append(label)
            spins.append(spin)
            self._rgb_spins.append(spin)
            self._controls.append(spin)

        left = self._square

        grid = Gtk.Grid(column_spacing=4, row_spacing=6, column_homogeneous=True,
                        valign=Gtk.Align.START)
        self._swatches: list[tuple[Gtk.Button, palette.RGB]] = []
        for i, (name, rgb) in enumerate(palette.SWATCHES):
            button = self._build_swatch(name, rgb)
            grid.attach(button, i % palette.GRID_COLUMNS, i // palette.GRID_COLUMNS, 1, 1)
            self._swatches.append((button, rgb))
            self._controls.append(button)
        presets_title = Gtk.Label(label="Presets", xalign=0.0)
        presets_title.add_css_class("metric-sub")
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right.append(presets_title)
        right.append(grid)
        spins.set_margin_top(4)
        right.append(spins)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        row.append(left)
        row.append(right)
        return row

    def _build_brightness(self) -> Gtk.Widget:
        label = Gtk.Label(label="Brightness", xalign=0.0)
        label.add_css_class("row-label")
        self._brightness = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        # No number until the daemon tells us one, so the slider never claims a
        # brightness we have not been told about (the hero shows "-" likewise).
        self._brightness.set_draw_value(False)
        self._brightness.set_value_pos(Gtk.PositionType.RIGHT)
        self._brightness.set_format_value_func(lambda _s, v: f"{int(v)} %")
        self._brightness.set_hexpand(True)
        self._brightness.connect("value-changed", self._on_brightness_changed)
        self._brightness.update_property([Gtk.AccessibleProperty.LABEL], ["Brightness"])
        self._controls.append(self._brightness)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.append(label)
        row.append(self._brightness)
        return row

    def _build_effects(self) -> Gtk.Widget:
        """Effect picker, in the same linked-button style as the fan profiles."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        row.add_css_class("linked")
        self._effect_buttons: dict[str, Gtk.ToggleButton] = {}
        group: Gtk.ToggleButton | None = None
        for effect in lighting.EFFECTS:
            button = Gtk.ToggleButton()
            button.add_css_class("profile-btn")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            name = Gtk.Label(label=effect.label)
            name.add_css_class("profile-name")
            sub = Gtk.Label(label=effect.subtitle)
            sub.add_css_class("profile-sub")
            inner.append(name)
            inner.append(sub)
            button.set_child(inner)
            button.set_tooltip_text(f"{effect.label} — {effect.subtitle}")
            if group is None:
                group = button
            else:
                button.set_group(group)
            button.connect("toggled", self._on_effect_toggled, effect.id)
            row.append(button)
            self._effect_buttons[effect.id] = button
            self._controls.append(button)

        row.update_property([Gtk.AccessibleProperty.LABEL], ["Effect"])
        self._effects_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._effects_box.append(row)
        return self._effects_box

    def _build_light_settings(self) -> Gtk.Widget:
        """Speed, auto-off and notification flash, as native settings rows."""
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class("boxed-list")

        # Speed: only meaningful for cycle and breathing, so hidden otherwise.
        self._speed_values: list[int] = []
        self._speed = Gtk.DropDown(valign=Gtk.Align.CENTER)
        self._speed.connect("notify::selected", self._on_speed_selected)
        self._speed.update_property([Gtk.AccessibleProperty.LABEL], ["Effect speed"])
        self._controls.append(self._speed)
        self._speed_row = Adw.ActionRow(title="Speed",
                                        subtitle="How long one full loop takes")
        self._speed_row.add_suffix(self._speed)
        rows.append(self._speed_row)

        self._idle_values: list[int] = []
        self._idle = Gtk.DropDown(valign=Gtk.Align.CENTER)
        self._idle.connect("notify::selected", self._on_idle_selected)
        self._idle.update_property([Gtk.AccessibleProperty.LABEL],
                                   ["Turn off when not typing"])
        self._controls.append(self._idle)
        self._set_dropdown(self._idle, self._idle_values,
                           [(v, lighting.duration_text(v)) for v in lighting.IDLE_CHOICES], 0)
        idle_row = Adw.ActionRow(
            title="Turn off when not typing",
            subtitle="The keys fade to dark. Any key brings them back.")
        idle_row.add_suffix(self._idle)
        rows.append(idle_row)

        self._flash_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self._flash_switch.connect("notify::active", self._on_flash_toggled)
        self._flash_switch.update_property([Gtk.AccessibleProperty.LABEL],
                                           ["Flash on notifications"])
        self._controls.append(self._flash_switch)
        self._flash_try = Gtk.Button(label="Try it", valign=Gtk.Align.CENTER)
        self._flash_try.add_css_class("flat")
        self._flash_try.set_tooltip_text("Flash the keys once now")
        self._flash_try.connect("clicked", lambda _b: self._client.flash_keyboard())
        self._controls.append(self._flash_try)
        flash_row = Adw.ActionRow(
            title="Flash on notifications",
            subtitle="The keys blink white when a message or alert arrives")
        flash_row.add_suffix(self._flash_try)
        flash_row.add_suffix(self._flash_switch)
        flash_row.set_activatable_widget(self._flash_switch)
        rows.append(flash_row)

        self._light_settings = rows
        return rows

    def _set_dropdown(self, dropdown: Gtk.DropDown, values: list[int],
                      choices: list[tuple[int, str]], current: int) -> None:
        """Refill a dropdown only when its choices changed, then select
        ``current``. Programmatic: guarded so it is not mistaken for a click."""
        was_syncing, self._syncing = self._syncing, True
        try:
            if [v for v, _ in choices] != values:
                values[:] = [v for v, _ in choices]
                dropdown.set_model(Gtk.StringList.new([label for _, label in choices]))
            if current in values:
                dropdown.set_selected(values.index(current))
        finally:
            self._syncing = was_syncing

    def _build_swatch(self, name: str, rgb: palette.RGB) -> Gtk.Button:
        """A button that paints itself in the exact colour it will send, so the
        swatch is a truthful sample rather than a themed approximation."""
        area = Gtk.DrawingArea()
        area.set_content_width(26)
        area.set_content_height(26)
        area.set_draw_func(self._draw_swatch, rgb)

        button = Gtk.Button(child=area, halign=Gtk.Align.CENTER,
                            valign=Gtk.Align.CENTER)
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
        cr.arc(width / 2, height / 2, min(width, height) / 2 - 1, 0, math.tau)
        cr.fill()

    # --- control handlers ----------------------------------------------------
    def _on_profile_toggled(self, button: Gtk.ToggleButton, profile_id: str) -> None:
        if self._syncing or not button.get_active():
            return
        self._client.set_profile(profile_id)

    def _on_custom_clicked(self, _button) -> None:
        self._curve_page.sync_from_daemon()
        self._nav.push_by_tag("curve")

    def _on_switch_toggled(self, switch: Gtk.Switch, _param) -> None:
        if self._syncing:
            return
        self._client.set_keyboard_enabled(switch.get_active())
        self._echo_preview()

    def _on_swatch_clicked(self, _button, rgb: palette.RGB) -> None:
        if self._syncing:
            return
        self._apply_colour(rgb)

    def _on_square_pick(self, rgb: palette.RGB, final: bool) -> None:
        if self._syncing:
            return
        self._queue_colour(rgb, now=final)

    def _on_rgb_spin(self, _spin) -> None:
        if self._syncing:
            return
        self._queue_colour(tuple(int(s.get_value()) for s in self._rgb_spins))

    def _queue_colour(self, rgb: palette.RGB, *, now: bool = False) -> None:
        """Show a colour at once, send it after a pause (or ``now``)."""
        self._pending_colour = rgb
        self._echo_colour(rgb)
        if self._colour_source:
            GLib.source_remove(self._colour_source)
            self._colour_source = 0
        if now:
            self._send_colour()
        else:
            self._colour_source = GLib.timeout_add(_BRIGHTNESS_DEBOUNCE_MS,
                                                   self._send_colour)

    def _send_colour(self) -> bool:
        self._colour_source = 0
        pending, self._pending_colour = self._pending_colour, None
        if pending is not None:
            self._apply_colour(pending)
        return GLib.SOURCE_REMOVE

    def _apply_colour(self, rgb: palette.RGB) -> None:
        self._client.set_keyboard_color(*rgb)
        self._echo_colour(rgb)

    def _echo_colour(self, rgb: palette.RGB) -> None:
        # Setting a colour also turns the backlight on daemon-side, so show that
        # here too instead of leaving the preview dark for a round-trip.
        was_syncing, self._syncing = self._syncing, True
        try:
            self._switch.set_active(True)
        finally:
            self._syncing = was_syncing
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

    def _on_effect_toggled(self, button: Gtk.ToggleButton, effect_id: str) -> None:
        if self._syncing or not button.get_active():
            return
        # Switching effect restarts at that effect's default speed: a speed is
        # "seconds per loop", and a 5-minute breath would look broken.
        self._client.set_keyboard_effect(effect_id, 0)

    def _on_speed_selected(self, dropdown: Gtk.DropDown, _param) -> None:
        if self._syncing:
            return
        i = dropdown.get_selected()
        effect = self._current_effect()
        if effect and 0 <= i < len(self._speed_values):
            self._client.set_keyboard_effect(effect, self._speed_values[i])

    def _on_idle_selected(self, dropdown: Gtk.DropDown, _param) -> None:
        if self._syncing:
            return
        i = dropdown.get_selected()
        if 0 <= i < len(self._idle_values):
            self._client.set_keyboard_idle_timeout(self._idle_values[i])

    def _on_flash_toggled(self, switch: Gtk.Switch, _param) -> None:
        if self._syncing:
            return
        self._client.set_keyboard_notify_flash(switch.get_active())

    def _current_effect(self) -> str | None:
        for effect_id, button in self._effect_buttons.items():
            if button.get_active():
                return effect_id
        return None

    # --- daemon state --------------------------------------------------------
    def _sync_from_daemon(self) -> None:
        """Pull the authoritative state back out of the daemon's properties."""
        self._syncing = True
        try:
            # The EC can start refusing writes, or start accepting them again,
            # while the window is open; the banner is how the user finds out.
            self._update_banner()
            profile = self._client.active_profile() or self._last_profile
            if profile:
                self._last_profile = profile
                shown = GLib.markup_escape_text(profiles.display_name(profile))
                self._now_label.set_markup(f"Now: <b>{shown}</b>")
                target = self._profile_buttons.get(profile)
                if target is not None:
                    target.set_active(True)  # the group clears the others
                else:
                    # custom / manual: no toggle represents it, so show none
                    # selected rather than pretending one of these is active
                    for button in self._profile_buttons.values():
                        button.set_active(False)
                # Custom... is a plain button, so it cannot be :checked; mark it
                # so the row still shows where the machine actually is.
                if profile == profiles.CUSTOM:
                    self._custom_button.add_css_class("chosen")
                else:
                    self._custom_button.remove_css_class("chosen")
            self._curve_page.sync_from_daemon()
            keyboard = self._client.keyboard_state()
            effects = self._client.keyboard_effects()
            self._sync_light_settings(effects)
            if keyboard:
                self._switch.set_active(keyboard.enabled)
                if self._brightness_source == 0:
                    # don't yank the slider out from under a drag that has not
                    # reached the daemon yet
                    self._brightness.set_value(keyboard.brightness_pct)
                    self._brightness.set_draw_value(True)  # now it is real
                if self._colour_source == 0:
                    # mid-drag: don't yank the marker back to the old colour
                    self._select_swatch(keyboard.rgb)
                    self._echo_preview(keyboard.rgb)
        finally:
            self._syncing = False

    def _sync_light_settings(self, effects) -> None:
        """Show the daemon's effect, speed, auto-off and flash settings. A
        daemon too old to have them reports none, and the controls hide rather
        than offer what it would refuse."""
        supported = effects is not None
        self._effects_box.set_visible(supported)
        self._light_settings.set_visible(supported)
        if not supported:
            return
        button = self._effect_buttons.get(effects.effect)
        if button is not None:
            button.set_active(True)
        speeds = lighting.speed_choices(effects.effect, effects.period_s)
        self._speed_row.set_visible(bool(speeds))
        if speeds:
            self._set_dropdown(self._speed, self._speed_values, speeds, effects.period_s)
        self._set_dropdown(
            self._idle, self._idle_values,
            [(v, lighting.duration_text(v))
             for v in lighting.idle_choices(effects.idle_timeout_s)],
            effects.idle_timeout_s)
        self._flash_switch.set_active(effects.notify_flash)

    def _select_swatch(self, rgb: palette.RGB) -> None:
        match = palette.name_for(rgb)
        self._colour_label.set_text(self._describe_light(match or "Custom colour"))
        # Cycle and CPU heat choose their own colours, so no swatch is "on".
        effects = self._client.keyboard_effects()
        colour_in_use = effects is None or effects.effect not in (
            lighting.CYCLE, lighting.TEMPERATURE)
        for button, value in self._swatches:
            chosen = colour_in_use and bool(match) and value == tuple(rgb)
            if chosen:
                button.add_css_class("selected")
            else:
                button.remove_css_class("selected")
            # The outline is decoration; this is what a screen reader reads —
            # PRESSED mirrors the mockup's aria-pressed on the active swatch.
            # The value must be a plain int: GTK reads it with g_value_get_int,
            # so a bool or the bare enum is rejected at runtime.
            button.update_state([Gtk.AccessibleState.PRESSED], [int(
                Gtk.AccessibleTristate.TRUE if chosen else Gtk.AccessibleTristate.FALSE)])
        self._shown_rgb = tuple(int(c) for c in rgb)
        was_syncing = self._syncing
        self._syncing = True  # setting a spin re-enters _on_rgb_spin
        try:
            if colour_in_use:
                self._square.show_colour(rgb)
            else:
                self._square.clear()  # cycle/heat pick their own colours
            for spin, value in zip(self._rgb_spins, rgb):
                spin.set_value(value)
        finally:
            self._syncing = was_syncing

    def _describe_light(self, colour_name: str) -> str:
        """The header's words for what the keys are doing — colour is never
        the only signal (DESIGN.md a11y), and neither is motion."""
        effects = self._client.keyboard_effects()
        if effects is None or effects.effect == lighting.STATIC:
            text = colour_name
        elif effects.effect in (lighting.CYCLE, lighting.TEMPERATURE):
            text = lighting.effect_label(effects.effect)
        else:
            text = f"{colour_name} · {lighting.effect_label(effects.effect).lower()}"
        if self._client.keyboard_idle():
            text += " · dark until you type"
        return text

    def _echo_preview(self, rgb: palette.RGB | None = None) -> None:
        """Repaint the preview from what the widgets currently show."""
        if rgb is None:
            rgb = self._shown_rgb
        effects = self._client.keyboard_effects()
        dimmed = self._client.keyboard_idle()
        self._preview.update(rgb, enabled=self._switch.get_active(),
                             brightness_pct=int(self._brightness.get_value()),
                             effect=effects.effect if effects else lighting.STATIC,
                             period_s=effects.period_s if effects else 0,
                             dimmed=dimmed)
        # Why the keys are dark is shown on the preview itself (its tooltip)
        # and in the header line; the auto-off row says the timeout.

    # --- live updates --------------------------------------------------------
    def _on_telemetry(self, t: Telemetry) -> None:
        self._preview.set_cpu_temp(t.cpu_temp)
        self._cpu_gauge.set_temp(t.cpu_temp)
        self._gpu_gauge.set_temp(t.gpu_temp)
        text, band = temperature.summary(t.cpu_temp, t.gpu_temp)
        self._set_pill(text, band.value)  # band.value == the css class
        self._cpu_rpm.set_text(_rpm(t.fan1_rpm))
        self._gpu_rpm.set_text(_rpm(t.fan2_rpm))
        self._cpu_duty.set_text(f"{t.fan1_duty_pct}% duty")
        self._gpu_duty.set_text(f"{t.fan2_duty_pct}% duty")
        self._curve_page.on_telemetry(t)

    def _on_availability(self, available: bool) -> None:
        if not available:
            self._show_waiting()
        self._update_sensitivity()

    def _update_sensitivity(self) -> None:
        """Controls need a daemon to talk to, and an unsupported machine must
        not be written to at all (DESIGN.md / PRODUCT.md safety invariants)."""
        supported = self._model.support is not Support.UNSUPPORTED
        usable = supported and self._client.available
        for widget in self._controls:
            widget.set_sensitive(usable)
        self._curve_page.set_controls_sensitive(usable)

    def show_error(self, message: str) -> None:
        """A refused or rejected write surfaces inline, never silently — and the
        controls snap back, so a failed click never leaves a button claiming
        something the daemon did not do. Public because the app routes
        tray-originated failures here too when the window is on screen."""
        # timeout=0: a write that was refused and auto-reverted is not a
        # transient nicety, so the notice stays until it is dismissed.
        self._toasts.add_toast(Adw.Toast(title=message, timeout=0))
        # Deferred: correcting a grouped ToggleButton from inside its own
        # "toggled" emission does not stick, so resync once GTK is done.
        if self._resync_source == 0:
            self._resync_source = GLib.idle_add(self._resync)

    def _resync(self) -> bool:
        self._resync_source = 0
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
