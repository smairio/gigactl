"""The trayed-app lifetime rules. Pure, because the alternative — a desktop with
no SNI host — cannot be reproduced faithfully: dbus-run-session removes the whole
session (portals, a11y, secrets), and GTK then blocks on those during startup."""
from gigactl_gui import lifetime


# --- should_hold ------------------------------------------------------------

def test_held_while_a_tray_icon_is_showing():
    assert lifetime.should_hold(tray_available=True, waiting_for_tray=False,
                                window_visible=True) is True


def test_held_while_still_waiting_to_learn_about_the_tray():
    # the regression this exists for: a --tray launch that takes no hold dies
    # before the tray can answer, so nothing is ever shown
    assert lifetime.should_hold(tray_available=False, waiting_for_tray=True,
                                window_visible=False) is True


def test_held_while_nothing_is_on_screen():
    # trayed app, host went away (screen lock): stay alive to re-register
    assert lifetime.should_hold(tray_available=False, waiting_for_tray=False,
                                window_visible=False) is True


def test_not_held_once_a_window_is_carrying_the_app():
    # a visible window keeps GApplication alive by itself, so let go
    assert lifetime.should_hold(tray_available=False, waiting_for_tray=False,
                                window_visible=True) is False


# --- should_offer_window ----------------------------------------------------

def test_offers_a_window_when_no_host_ever_answered():
    assert lifetime.should_offer_window(tray_ever_registered=False,
                                        tray_available=False,
                                        window_visible=False) is True


def test_does_not_offer_a_window_when_a_host_answered_before():
    # GNOME disables extensions at the lock screen: the watcher disappears while
    # the session is locked, and a window must not be pushed over it
    assert lifetime.should_offer_window(tray_ever_registered=True,
                                        tray_available=False,
                                        window_visible=False) is False


def test_does_not_offer_a_second_window():
    assert lifetime.should_offer_window(tray_ever_registered=False,
                                        tray_available=False,
                                        window_visible=True) is False


def test_does_not_offer_a_window_while_the_tray_is_working():
    assert lifetime.should_offer_window(tray_ever_registered=True,
                                        tray_available=True,
                                        window_visible=False) is False


# --- should_quit_on_close --------------------------------------------------

def test_closing_quits_when_there_is_no_tray():
    assert lifetime.should_quit_on_close(tray_available=False) is True


def test_closing_only_hides_when_a_tray_can_reopen_it():
    assert lifetime.should_quit_on_close(tray_available=True) is False


# --- the sequences the rules exist for -------------------------------------

def test_tray_only_launch_on_a_desktop_with_no_host():
    """--tray where nothing ever answers: hold, wait, then show a window."""
    assert lifetime.should_offer_window(tray_ever_registered=False,
                                        tray_available=False, window_visible=False)
    assert lifetime.should_hold(tray_available=False, waiting_for_tray=True,
                                window_visible=False)
    # once the window is up it carries the app, and closing it ends things
    assert not lifetime.should_hold(tray_available=False, waiting_for_tray=False,
                                    window_visible=True)
    assert lifetime.should_quit_on_close(tray_available=False)


def test_screen_lock_while_trayed_is_survived_quietly():
    """The host answered, then vanished with the window hidden."""
    assert lifetime.should_hold(tray_available=False, waiting_for_tray=False,
                                window_visible=False)          # stay alive…
    assert not lifetime.should_offer_window(tray_ever_registered=True,
                                            tray_available=False,
                                            window_visible=False)  # …but quietly
