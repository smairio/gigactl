"""GigaControl application: stylesheet, model detection, window, tray.

The app owns the tray icon and, so the tray can act while no window exists, its
own small connection to the daemon. Lifetime is the fiddly part, because a trayed
app has no window to keep it alive and GApplication stops as soon as nothing holds
it. Two rules cover it:

* the app is **held** while a tray icon is showing, while still waiting to find
  out whether one will appear, and while nothing is on screen at all. A visible
  window needs no hold — it is its own.
* a window is offered only when no host has **ever** answered. One that answered
  and then vanished is waited out silently, because GNOME disables extensions at
  the lock screen and a window must not appear over it.

So while a tray is present, closing the window merely hides it and reopening is
instant; with no tray host at all, ``--tray`` shows the window instead of becoming
an invisible process, and closing it then really quits.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import model as model_mod  # noqa: E402
from . import lifetime, profiles, tray_menu  # noqa: E402
from .client import DaemonClient  # noqa: E402
from .tray import TrayIcon  # noqa: E402
from .window import OverviewWindow  # noqa: E402

APP_ID = "io.github.smairio.gigactl"

# How long a --tray launch waits for a tray host before giving up and showing the
# window instead of leaving the user with nothing at all.
_TRAY_GRACE_MS = 2500

# Only the app-specific bits; everything structural uses libadwaita's own named
# colours so the theme (and light/dark) follow the platform.
_CSS = """
.verbar { padding: 10px 14px; border-radius: 9px; font-weight: 700; }
.verbar.verified   { background-color: @success_bg_color; color: @success_fg_color; }
.verbar.expected   { background-color: @warning_bg_color; color: @warning_fg_color; }
.verbar.unsupported{ background-color: @error_bg_color;   color: @error_fg_color; }
.gauge-num { font-size: 32px; font-weight: 800; font-feature-settings: "tnum"; }
.gauge-label { font-size: 11px; font-weight: 800; letter-spacing: 0.06em; opacity: 0.55; }
.status-pill { padding: 5px 12px; border-radius: 99px; font-weight: 700; font-size: 12px; }
.status-pill.cool { background-color: alpha(@success_color, 0.16); color: @success_color; }
.status-pill.warm { background-color: alpha(@warning_color, 0.16); color: @warning_color; }
.status-pill.hot  { background-color: alpha(@error_color, 0.16);   color: @error_color; }
.rpm-value { font-size: 20px; font-weight: 800; font-feature-settings: "tnum"; }
.eyebrow { font-size: 11px; font-weight: 800; letter-spacing: 0.06em; opacity: 0.55; }
.metric-sub { font-size: 12px; opacity: 0.6; }
.row-label { font-size: 13.5px; font-weight: 600; }
.sec { font-size: 16px; font-weight: 800; }

/* Fan-profile row: the active profile fills with accent; Firmware fills
   neutral, because yielding to the hardware is a state, not a setting. */
.profile-btn { padding: 8px 10px; }
.profile-name { font-size: 13.5px; font-weight: 700; }
.profile-sub { font-size: 11px; opacity: 0.6; }
.profile-btn:checked { background-color: @accent_bg_color; color: @accent_fg_color; }
.profile-btn:checked .profile-sub { opacity: 0.85; }
.profile-btn.firmware:checked {
  background-color: alpha(@window_fg_color, 0.16); color: @window_fg_color;
}

/* Colour swatches: the button is just a frame — the circle inside is painted in
   the exact RGB that will be sent to the backlight. */
.swatch { padding: 3px; min-width: 26px; min-height: 26px; border-radius: 99px; }
.swatch.selected { outline: 2px solid @accent_color; outline-offset: 1px; }

/* Custom… is a plain button (it opens the curve editor), so it cannot be
   :checked — .chosen gives it the same fill when that curve is what is running. */
.profile-btn.chosen { background-color: @accent_bg_color; color: @accent_fg_color; }
.profile-btn.chosen .profile-sub { opacity: 0.85; }

/* Curve editor */
.curvebox {
  background-color: @card_bg_color; border-radius: 11px; padding: 14px;
}
.axis-tick { font-size: 10.5px; opacity: 0.55; font-feature-settings: "tnum"; }
/* the curve plot reads its own colour to paint with (see curve_view._accent) */
.curve-accent { color: @accent_color; }
"""


class GigactlApp(Adw.Application):
    def __init__(self, start_hidden: bool = False) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self._window: OverviewWindow | None = None
        self._start_hidden = start_hidden
        self._tray: TrayIcon | None = None
        self._tray_client: DaemonClient | None = None
        self._held = False
        self._grace_source = 0
        # Whether a tray host has *ever* answered. "Never answered" earns a
        # window; "answered then went away" (a screen lock, a shell restart) is
        # waited out silently — see lifetime.should_offer_window.
        self._tray_ever_registered = False

    # --- lifecycle -----------------------------------------------------------
    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_string(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        # GTK4 widgets have no destroy signal; the application tells us instead,
        # so a closed window never leaves a stale reference behind.
        self.connect("window-removed", self._on_window_removed)
        self._start_tray()

    def do_activate(self) -> None:
        if self._window is None:
            self._window = OverviewWindow(self, model_mod.detect())
            self._apply_close_policy()
        if self._start_hidden:
            # Launched to the tray: build the window but leave it hidden until
            # asked for. A later activation (tray click, second launch) shows it.
            self._start_hidden = False
            self._sync_hold()
            return
        self._window.present()
        self._sync_hold()

    def do_shutdown(self) -> None:
        if self._grace_source:
            GLib.source_remove(self._grace_source)
            self._grace_source = 0
        if self._tray:
            self._tray.stop()
        Adw.Application.do_shutdown(self)

    # --- tray ----------------------------------------------------------------
    def _start_tray(self) -> None:
        """The tray gets its own daemon connection: it has to work (and report
        failures) while the window is hidden or not yet built."""
        self._tray_client = DaemonClient(on_availability=lambda _a: self._refresh_tray(),
                                         on_state=self._refresh_tray,
                                         on_error=self._notify_error)
        self._tray = TrayIcon(APP_ID, on_action=self._on_tray_action,
                              on_available=self._on_tray_available)
        self._tray.start()
        self._tray_client.start()
        if self._start_hidden:
            # Nothing is on screen yet, so without a hold GApplication would see
            # a use count of zero and stop before the tray could even answer.
            self._await_tray()

    def _await_tray(self) -> None:
        """Give a tray host a moment to answer; if none ever does, the grace
        timer shows the window instead (see :mod:`lifetime`)."""
        if self._grace_source or not self._should_offer_window():
            self._sync_hold()
            return
        self._grace_source = GLib.timeout_add(_TRAY_GRACE_MS, self._grace_expired)
        self._sync_hold()

    def _should_offer_window(self) -> bool:
        return lifetime.should_offer_window(
            tray_ever_registered=self._tray_ever_registered,
            tray_available=self._tray_showing(),
            window_visible=self._window_visible())

    def _tray_showing(self) -> bool:
        return self._tray is not None and self._tray.available

    def _window_visible(self) -> bool:
        return self._window is not None and self._window.get_visible()

    def _grace_expired(self) -> bool:
        self._grace_source = 0
        if self._should_offer_window():
            print("gigactl-gui: no tray host on this session, showing the window "
                  "instead", flush=True)
            self._start_hidden = False
            self.activate()
        self._sync_hold()
        return GLib.SOURCE_REMOVE

    def _sync_hold(self) -> None:
        wanted = lifetime.should_hold(tray_available=self._tray_showing(),
                                      waiting_for_tray=bool(self._grace_source),
                                      window_visible=self._window_visible())
        if wanted and not self._held:
            self.hold()
            self._held = True
        elif not wanted and self._held:
            self.release()
            self._held = False

    def _on_tray_available(self, available: bool) -> None:
        if available:
            self._tray_ever_registered = True
            if self._grace_source:
                GLib.source_remove(self._grace_source)
                self._grace_source = 0
        self._apply_close_policy()
        # Every path ends here: skipping it once left the tray's hold in place
        # after the icon went away, and the app outlived its last window.
        self._sync_hold()
        if available:
            self._refresh_tray()

    def _apply_close_policy(self) -> None:
        """With a tray to reopen from, closing the window only hides it."""
        if self._window is not None:
            self._window.set_hide_on_close(not lifetime.should_quit_on_close(
                tray_available=self._tray_showing()))

    def _on_window_removed(self, _app, window) -> None:
        if window is not self._window:
            return
        self._window = None
        if lifetime.should_quit_on_close(tray_available=self._tray_showing()):
            # No icon to come back from, so the close was a real goodbye rather
            # than a hide — going on would leave a process nobody can reach.
            self.quit()
            return
        self._sync_hold()

    def _refresh_tray(self) -> None:
        if not self._tray or not self._tray_client:
            return
        client = self._tray_client
        keyboard = client.keyboard_state()
        profile = client.active_profile()
        self._tray.update(
            active_profile=profile,
            keyboard_on=None if keyboard is None else keyboard.enabled,
            available=client.available,
            detail=(f"Fan profile: {profiles.display_name(profile)}" if profile
                    else "Waiting for the gigactl daemon…"))

    def _on_tray_action(self, action: str) -> None:
        client = self._tray_client
        if action == tray_menu.ACTION_OPEN:
            self.activate()
            return
        if action == tray_menu.ACTION_QUIT:
            self.quit()
            return
        if client is None:
            return
        if action == tray_menu.ACTION_KEYBOARD:
            current = client.keyboard_state()
            client.set_keyboard_enabled(not (current.enabled if current else True))
            return
        profile = tray_menu.profile_of(action)
        if profile:
            client.set_profile(profile)

    def _notify_error(self, message: str) -> None:
        """A tray action can fail with no window on screen, so say it where the
        user will actually see it."""
        if self._window_visible():
            self._window.show_error(message)
            return
        notification = Gio.Notification.new("GigaControl")
        notification.set_body(message)
        self.send_notification("gigactl-error", notification)
