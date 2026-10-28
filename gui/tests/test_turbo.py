"""The CPU turbo row: when the window shows it, and when it can be used.

Pure logic, so it is tested here rather than through GTK. The window applies
the answer in two places — on every daemon update and whenever the daemon comes
or goes — which is exactly why it is decided once, here.
"""
from gigactl_gui import turbo


def test_a_machine_without_the_switch_shows_no_turbo_row():
    """Honest about hardware (PRODUCT.md #3): never offer a control the
    machine lacks."""
    assert turbo.row(state=(False, False), usable=True).visible is False


def test_an_older_daemon_or_none_at_all_shows_no_turbo_row():
    """Unknown is not the same as present: a daemon from before turbo support,
    or no daemon, must not make the window invent a switch."""
    assert turbo.row(state=None, usable=True).visible is False


def test_the_switch_follows_the_daemon_and_works_when_controls_do():
    shown = turbo.row(state=(True, False), usable=True)
    assert (shown.visible, shown.sensitive, shown.active) == (True, True, False)


def test_the_switch_is_shown_but_locked_when_controls_are():
    """No daemon to talk to, or a machine we refuse to write to: the row stays
    visible (the machine has the switch) but cannot be used — the same rule
    as every other control on the Overview."""
    locked = turbo.row(state=(True, True), usable=False)
    assert (locked.visible, locked.sensitive) == (True, False)
