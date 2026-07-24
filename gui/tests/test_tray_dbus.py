"""Every tray reply must match the signature its own introspection XML declares.

This is the test that was missing when ``GetProperty`` shipped replying ``(s)``
where it had declared ``(v)`` — a mismatch GDBus rejects at runtime but nothing in
the code could catch. Rather than hard-coding the expected signatures, each one is
read back out of the XML, so the declaration and the implementation cannot drift
apart.

No bus and no display: the handlers are called directly with a recording stand-in
for the method invocation, exactly as Gio would call them.
"""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

from gigactl_gui import tray  # noqa: E402

ITEM_IFACE = "org.kde.StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"


class Invocation:
    """Stands in for Gio's method invocation."""

    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, value):
        self.value = value

    def return_dbus_error(self, name, message):
        self.error = (name, message)


def _interface(xml: str, name: str) -> Gio.DBusInterfaceInfo:
    return Gio.DBusNodeInfo.new_for_xml(xml).lookup_interface(name)


def _declared_reply(xml: str, iface: str, method: str) -> str:
    """The reply signature the XML promises for this method."""
    info = _interface(xml, iface).lookup_method(method)
    return "(" + "".join(arg.signature for arg in info.out_args) + ")"


def _declared_property(xml: str, iface: str, prop: str) -> str:
    return _interface(xml, iface).lookup_property(prop).signature


def _icon() -> tray.TrayIcon:
    icon = tray.TrayIcon("io.github.smairio.gigactl", on_action=lambda _a: None)
    icon.update(active_profile="balanced", keyboard_on=True, available=True)
    return icon


def _call_menu(icon, method, params):
    invocation = Invocation()
    icon._menu_call(None, None, None, MENU_IFACE, method, params, invocation)
    assert invocation.error is None, f"{method} failed: {invocation.error}"
    return invocation.value


# --- method replies ---------------------------------------------------------

def test_get_layout_matches_its_declared_signature():
    reply = _call_menu(_icon(), "GetLayout", GLib.Variant("(iias)", (0, -1, [])))
    assert reply.get_type_string() == _declared_reply(tray._MENU_XML, MENU_IFACE,
                                                      "GetLayout")


def test_get_group_properties_matches_its_declared_signature():
    reply = _call_menu(_icon(), "GetGroupProperties",
                       GLib.Variant("(aias)", ([], [])))
    assert reply.get_type_string() == _declared_reply(tray._MENU_XML, MENU_IFACE,
                                                     "GetGroupProperties")


def test_get_property_matches_its_declared_signature():
    """The regression: a boxed 'v', not the bare value's own type."""
    reply = _call_menu(_icon(), "GetProperty", GLib.Variant("(is)", (1, "label")))
    assert reply.get_type_string() == _declared_reply(tray._MENU_XML, MENU_IFACE,
                                                      "GetProperty")
    assert reply.get_type_string() == "(v)"
    assert reply.unpack()[0]  # and it really carries the label


def test_event_group_matches_its_declared_signature():
    reply = _call_menu(_icon(), "EventGroup", GLib.Variant("(a(isvu))", ([],)))
    assert reply.get_type_string() == _declared_reply(tray._MENU_XML, MENU_IFACE,
                                                      "EventGroup")


def test_about_to_show_matches_its_declared_signature():
    reply = _call_menu(_icon(), "AboutToShow", GLib.Variant("(i)", (0,)))
    assert reply.get_type_string() == _declared_reply(tray._MENU_XML, MENU_IFACE,
                                                      "AboutToShow")


def test_event_dispatches_a_click_and_replies_with_nothing():
    seen = []
    icon = tray.TrayIcon("io.github.smairio.gigactl", on_action=seen.append)
    icon.update(active_profile="balanced", keyboard_on=True, available=True)
    quiet = next(i for i in icon._items if i.action == "profile:quiet")
    invocation = Invocation()
    icon._menu_call(None, None, None, MENU_IFACE, "Event",
                    GLib.Variant("(isvu)", (quiet.id, "clicked",
                                            GLib.Variant("i", 0), 0)), invocation)
    assert seen == ["profile:quiet"]
    assert invocation.value is None  # Event declares no out args


# --- properties -------------------------------------------------------------

def test_every_item_property_matches_its_declared_signature():
    icon = _icon()
    info = _interface(tray._ITEM_XML, ITEM_IFACE)
    assert info.properties, "expected the item interface to declare properties"
    for prop in info.properties:
        value = icon._item_property(None, None, None, ITEM_IFACE, prop.name)
        assert value is not None, f"{prop.name} returned nothing"
        assert value.get_type_string() == prop.signature, prop.name


def test_every_menu_property_matches_its_declared_signature():
    icon = _icon()
    info = _interface(tray._MENU_XML, MENU_IFACE)
    for prop in info.properties:
        value = icon._menu_property(None, None, None, MENU_IFACE, prop.name)
        assert value is not None, f"{prop.name} returned nothing"
        assert value.get_type_string() == prop.signature, prop.name


def test_an_unknown_property_is_none_rather_than_an_error():
    assert _icon()._item_property(None, None, None, ITEM_IFACE, "Nope") is None


def test_a_raising_property_getter_cannot_escape_into_the_dispatcher():
    icon = _icon()
    icon._items = None  # force the tooltip/menu lookups to blow up
    icon._detail = None
    def boom(_prop):
        raise RuntimeError("boom")
    icon._item_property_value = boom
    assert icon._item_property(None, None, None, ITEM_IFACE, "Title") is None


# --- the menu a host would draw ---------------------------------------------

def test_layout_lists_every_item_as_a_child_of_the_root():
    icon = _icon()
    reply = _call_menu(icon, "GetLayout", GLib.Variant("(iias)", (0, -1, [])))
    _revision, (root_id, root_props, children) = reply.unpack()
    assert root_id == 0
    assert root_props["children-display"] == "submenu"
    assert [child[0] for child in children] == [i.id for i in icon._items]
