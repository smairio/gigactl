"""When a trayed app should stay alive, and when it owes the user a window.

Two rules, kept here as plain functions because they are the easy thing to get
wrong and the hard thing to observe: GApplication stops the moment nothing holds
it, so a tray-only launch that forgets to hold simply dies before its tray can
answer — and one that holds forever with no icon and no window becomes a process
the user cannot see or reach.
"""
from __future__ import annotations


def should_hold(*, tray_available: bool, waiting_for_tray: bool) -> bool:
    """Whether to keep GApplication alive by hand.

    Held while a tray icon is showing (there is a way back), and while still
    waiting to learn whether one will appear (otherwise we never find out). A
    visible window needs no hold — it is its own.
    """
    return tray_available or waiting_for_tray


def is_unreachable(*, tray_available: bool, window_visible: bool) -> bool:
    """True when the user can see neither a tray icon nor a window.

    A transient state — a host may still be starting, and a GNOME Shell restart
    drops the watcher for a moment — but never one to settle in, so the caller
    waits briefly and then shows the window.
    """
    return not tray_available and not window_visible
