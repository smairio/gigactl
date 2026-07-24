"""What the tray menu contains, kept free of D-Bus so it can be tested.

On GNOME the indicator extension only ever shows the menu — both clicks open it
— so *every* action has to be a menu item (see ``research/tray-ubuntu-gtk4.md``
on branch ``research/tray-ubuntu-gtk4``). The menu is deliberately flat: a
submenu would cost an extra click and a hover on the one surface whose whole
purpose is to be quick.

:func:`properties_of` returns each item's ``com.canonical.dbusmenu`` properties
as ``{name: (signature, value)}``, so the D-Bus layer only has to wrap them in
variants and this module stays testable.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import profiles

SEPARATOR = "separator"
STANDARD = "standard"

ACTION_OPEN = "open"
ACTION_QUIT = "quit"
ACTION_KEYBOARD = "keyboard-toggle"
_PROFILE_PREFIX = "profile:"


@dataclass(frozen=True)
class MenuItem:
    id: int
    label: str = ""
    action: str | None = None
    kind: str = STANDARD
    toggle: str | None = None          # "radio" | "checkmark" | None
    checked: bool | None = None        # None = not known, which dbusmenu spells -1
    enabled: bool = True


def build(active_profile: str | None, keyboard_on: bool | None,
          available: bool) -> list[MenuItem]:
    """The whole menu. ``available`` is whether the daemon is reachable: without
    it the control items are shown but disabled, while opening the window and
    quitting always work."""
    items: list[MenuItem] = []
    next_id = 1

    def add(**kwargs) -> None:
        nonlocal next_id
        items.append(MenuItem(id=next_id, **kwargs))
        next_id += 1

    add(label="Open GigaControl", action=ACTION_OPEN)
    add(kind=SEPARATOR)
    for profile in profiles.SELECTABLE:
        add(label=profile.label, action=f"{_PROFILE_PREFIX}{profile.id}",
            toggle="radio", checked=profile.id == active_profile,
            enabled=available)
    add(kind=SEPARATOR)
    add(label="Keyboard light", action=ACTION_KEYBOARD, toggle="checkmark",
        checked=keyboard_on, enabled=available)
    add(kind=SEPARATOR)
    add(label="Quit", action=ACTION_QUIT)
    return items


def properties_of(item: MenuItem) -> dict[str, tuple[str, object]]:
    """The item's dbusmenu properties as {name: (dbus signature, value)}."""
    if item.kind == SEPARATOR:
        return {"type": ("s", SEPARATOR), "visible": ("b", True)}

    props: dict[str, tuple[str, object]] = {
        "label": ("s", item.label),
        "enabled": ("b", item.enabled),
        "visible": ("b", True),
    }
    if item.toggle:
        props["toggle-type"] = ("s", item.toggle)
        # -1 is dbusmenu's "indeterminate": we have not been told yet, which is
        # not the same as "off".
        props["toggle-state"] = ("i", -1 if item.checked is None
                                 else int(item.checked))
    return props


def dispatch(items: list[MenuItem], item_id: int) -> str | None:
    """The action for a clicked id, or None for separators and unknown ids."""
    for item in items:
        if item.id == item_id:
            return item.action
    return None


def profile_of(action: str) -> str | None:
    """The profile name in a ``profile:*`` action, or None for other actions."""
    if action.startswith(_PROFILE_PREFIX):
        return action[len(_PROFILE_PREFIX):]
    return None
