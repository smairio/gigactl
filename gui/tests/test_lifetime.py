"""The trayed-app lifetime rules. Pure, because the alternative — a desktop with
no SNI host — cannot be reproduced faithfully: dbus-run-session removes the whole
session (portals, a11y, secrets), and GTK then blocks on those during startup."""
from gigactl_gui import lifetime


# --- should_hold ------------------------------------------------------------

def test_held_while_a_tray_icon_is_showing():
    assert lifetime.should_hold(tray_available=True, waiting_for_tray=False) is True


def test_held_while_still_waiting_to_learn_about_the_tray():
    # the regression this exists for: a --tray launch that takes no hold dies
    # before the tray can answer, so nothing is ever shown
    assert lifetime.should_hold(tray_available=False, waiting_for_tray=True) is True


def test_not_held_once_there_is_no_tray_and_no_waiting():
    # a window is on screen by then, and a window keeps the app alive itself
    assert lifetime.should_hold(tray_available=False, waiting_for_tray=False) is False


# --- is_unreachable ---------------------------------------------------------

def test_unreachable_when_nothing_is_on_screen_and_no_tray():
    assert lifetime.is_unreachable(tray_available=False, window_visible=False) is True


def test_reachable_through_the_tray():
    assert lifetime.is_unreachable(tray_available=True, window_visible=False) is False


def test_reachable_through_the_window():
    assert lifetime.is_unreachable(tray_available=False, window_visible=True) is False


def test_reachable_through_both():
    assert lifetime.is_unreachable(tray_available=True, window_visible=True) is False


# --- the two rules together -------------------------------------------------

def test_a_tray_only_launch_holds_until_it_knows():
    """The full sequence a --tray launch goes through with no host present."""
    # nothing on screen yet: unreachable, so we wait — and must hold to do so
    assert lifetime.is_unreachable(tray_available=False, window_visible=False)
    assert lifetime.should_hold(tray_available=False, waiting_for_tray=True)
    # the wait ends with a window, and the hold is no longer needed
    assert not lifetime.is_unreachable(tray_available=False, window_visible=True)
    assert not lifetime.should_hold(tray_available=False, waiting_for_tray=False)
