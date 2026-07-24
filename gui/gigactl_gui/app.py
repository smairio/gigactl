"""GigaControl application: load the stylesheet, detect the model, show the
Overview window. libadwaita drives light/dark from the system automatically.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from . import model as model_mod  # noqa: E402
from .window import OverviewWindow  # noqa: E402

APP_ID = "io.github.smairio.gigactl"

# Only the app-specific bits; everything structural uses libadwaita's own named
# colours so the theme (and light/dark) follow the platform.
_CSS = """
.verbar { padding: 10px 14px; border-radius: 9px; font-weight: 700; }
.verbar.verified   { background-color: @success_bg_color; color: @success_fg_color; }
.verbar.expected   { background-color: @warning_bg_color; color: @warning_fg_color; }
.verbar.unsupported{ background-color: @error_bg_color;   color: @error_fg_color; }
.gauge-label { font-size: 11px; font-weight: 800; letter-spacing: 0.06em; opacity: 0.55; }
.status-pill { padding: 5px 12px; border-radius: 99px; font-weight: 700; font-size: 12px; }
.status-pill.cool { background-color: alpha(@success_color, 0.16); color: @success_color; }
.status-pill.warm { background-color: alpha(@warning_color, 0.16); color: @warning_color; }
.status-pill.hot  { background-color: alpha(@error_color, 0.16);   color: @error_color; }
.rpm-value { font-size: 20px; font-weight: 800; font-feature-settings: "tnum"; }
.eyebrow { font-size: 11px; font-weight: 800; letter-spacing: 0.06em; opacity: 0.55; }
.metric-sub { font-size: 12px; opacity: 0.6; }
"""


class GigactlApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self._window: OverviewWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_string(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self) -> None:
        if self._window is None:
            self._window = OverviewWindow(self, model_mod.detect())
        self._window.present()
