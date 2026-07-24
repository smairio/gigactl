"""The main window: the read-only Overview (model banner + live hero).

Layout follows DESIGN.md, top-to-bottom, beginner-first: the verification banner,
then the live-state hero (CPU/GPU ring gauges beside a status pill and per-fan
RPM + duty). Profile controls and the keyboard section are later tickets.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from . import temperature  # noqa: E402
from .client import DaemonClient, Telemetry  # noqa: E402
from .gauge import TempGauge  # noqa: E402
from .model import Model, Support  # noqa: E402

_SUPPORT_CSS = {
    Support.VERIFIED: "verified",
    Support.EXPECTED: "expected",
    Support.UNSUPPORTED: "unsupported",
}
_BAND_CSS = {
    temperature.Band.COOL: "cool",
    temperature.Band.WARM: "warm",
    temperature.Band.HOT: "hot",
}


def _rpm(value: int) -> str:
    return f"{value:,}".replace(",", " ")  # narrow no-break space groups


class OverviewWindow(Adw.ApplicationWindow):
    __gtype_name__ = "GigactlOverviewWindow"

    def __init__(self, app: Adw.Application, model: Model) -> None:
        super().__init__(application=app)
        self.set_title("GigaControl")
        self.set_default_size(720, 560)

        self._banner = self._build_banner(model)
        hero = self._build_hero()

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.append(self._banner)
        content.append(hero)

        clamp = Adw.Clamp(maximum_size=760, child=content)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      child=clamp, vexpand=True)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(scroller)
        self.set_content(toolbar)

        # Live data. The client's callbacks fire on the GLib main loop, so they
        # touch widgets directly.
        self._client = DaemonClient(on_telemetry=self._on_telemetry,
                                    on_availability=self._on_availability)
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
        box.add_css_class(_SUPPORT_CSS[model.support])
        box.append(Gtk.Image.new_from_icon_name(icon))
        label = Gtk.Label(label=model.banner, xalign=0.0, wrap=True)
        box.append(label)
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

    # --- live updates --------------------------------------------------------
    def _on_telemetry(self, t: Telemetry) -> None:
        self._cpu_gauge.set_temp(t.cpu_temp)
        self._gpu_gauge.set_temp(t.gpu_temp)
        text, band = temperature.summary(t.cpu_temp, t.gpu_temp)
        self._set_pill(text, _BAND_CSS[band])
        self._cpu_rpm.set_text(_rpm(t.fan1_rpm))
        self._gpu_rpm.set_text(_rpm(t.fan2_rpm))
        self._cpu_duty.set_text(f"{t.fan1_duty_pct}% duty")
        self._gpu_duty.set_text(f"{t.fan2_duty_pct}% duty")

    def _on_availability(self, available: bool) -> None:
        if not available:
            self._show_waiting()

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
        for css in _BAND_CSS.values():
            self._pill.remove_css_class(css)
        if band_css:
            self._pill.add_css_class(band_css)
