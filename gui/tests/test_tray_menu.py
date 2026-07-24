"""The tray menu's model: what it offers, what it marks as current, and which
action an item id maps to. Pure — the D-Bus plumbing is verified live."""
from gigactl_gui import tray_menu


def _labels(items):
    return [i.label for i in items if i.kind != "separator"]


def _by_action(items, action):
    return next(i for i in items if i.action == action)


# --- what the menu offers ---------------------------------------------------

def test_menu_offers_the_four_things_the_ticket_asks_for():
    items = tray_menu.build(active_profile="balanced", keyboard_on=True, available=True)
    actions = {i.action for i in items}
    assert "open" in actions            # open the window
    assert "quit" in actions            # quit
    assert "keyboard-toggle" in actions  # keyboard off
    assert {f"profile:{p}" for p in ("quiet", "balanced", "performance", "max",
                                     "firmware")} <= actions


def test_every_item_has_a_unique_id():
    items = tray_menu.build("balanced", True, True)
    ids = [i.id for i in items]
    assert len(ids) == len(set(ids))
    assert 0 not in ids  # 0 is the dbusmenu root


def test_separators_group_the_menu_but_carry_no_action():
    items = tray_menu.build("balanced", True, True)
    separators = [i for i in items if i.kind == "separator"]
    assert separators, "expected the menu to be grouped"
    assert all(s.action is None and not s.label for s in separators)


# --- reflecting the daemon's state ------------------------------------------

def test_the_active_profile_is_the_checked_radio():
    items = tray_menu.build("performance", True, True)
    active = _by_action(items, "profile:performance")
    assert active.toggle == "radio" and active.checked is True
    other = _by_action(items, "profile:quiet")
    assert other.toggle == "radio" and other.checked is False


def test_a_profile_with_no_menu_entry_checks_nothing():
    # 'custom' and 'manual' are real daemon states with no tray item
    for profile in ("custom", "manual"):
        items = tray_menu.build(profile, True, True)
        assert not any(i.checked for i in items if i.toggle == "radio")


def test_keyboard_item_is_a_checkmark_following_the_backlight():
    on = _by_action(tray_menu.build("balanced", True, True), "keyboard-toggle")
    assert on.toggle == "checkmark" and on.checked is True
    off = _by_action(tray_menu.build("balanced", False, True), "keyboard-toggle")
    assert off.checked is False


def test_unknown_keyboard_state_is_not_claimed():
    item = _by_action(tray_menu.build("balanced", None, True), "keyboard-toggle")
    assert item.checked is None  # nothing known yet, so claim nothing


def test_controls_are_disabled_with_no_daemon_but_the_window_still_opens():
    items = tray_menu.build(None, None, available=False)
    assert _by_action(items, "profile:quiet").enabled is False
    assert _by_action(items, "keyboard-toggle").enabled is False
    assert _by_action(items, "open").enabled is True  # always reachable
    assert _by_action(items, "quit").enabled is True


# --- the dbusmenu property mapping ------------------------------------------

def test_properties_describe_a_plain_item():
    item = _by_action(tray_menu.build("balanced", True, True), "open")
    props = tray_menu.properties_of(item)
    assert props["label"] == ("s", item.label)
    assert props["enabled"] == ("b", True)
    assert props["visible"] == ("b", True)
    assert "toggle-type" not in props


def test_properties_describe_a_separator():
    sep = next(i for i in tray_menu.build("balanced", True, True)
               if i.kind == "separator")
    assert tray_menu.properties_of(sep)["type"] == ("s", "separator")


def test_properties_carry_the_toggle_state():
    radio = _by_action(tray_menu.build("quiet", True, True), "profile:quiet")
    props = tray_menu.properties_of(radio)
    assert props["toggle-type"] == ("s", "radio")
    assert props["toggle-state"] == ("i", 1)

    unchecked = tray_menu.properties_of(
        _by_action(tray_menu.build("quiet", True, True), "profile:max"))
    assert unchecked["toggle-state"] == ("i", 0)


def test_unknown_toggle_state_is_reported_as_indeterminate():
    item = _by_action(tray_menu.build("balanced", None, True), "keyboard-toggle")
    # dbusmenu spells "don't know" as -1, which is not the same as "off"
    assert tray_menu.properties_of(item)["toggle-state"] == ("i", -1)


# --- dispatch ---------------------------------------------------------------

def test_dispatch_maps_an_id_back_to_its_action():
    items = tray_menu.build("balanced", True, True)
    target = _by_action(items, "profile:max")
    assert tray_menu.dispatch(items, target.id) == "profile:max"


def test_dispatch_ignores_unknown_and_actionless_ids():
    items = tray_menu.build("balanced", True, True)
    separator = next(i for i in items if i.kind == "separator")
    assert tray_menu.dispatch(items, separator.id) is None
    assert tray_menu.dispatch(items, 9999) is None


def test_profile_of_extracts_the_name():
    assert tray_menu.profile_of("profile:quiet") == "quiet"
    assert tray_menu.profile_of("quit") is None
