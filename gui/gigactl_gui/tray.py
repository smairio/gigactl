"""StatusNotifierItem tray icon, spoken directly over D-Bus.

GTK4 has no tray API and ``libayatana-appindicator`` is GTK3-only, so the
supported route on Ubuntu is to implement the protocol its indicator extension
actually consumes: ``org.kde.StatusNotifierItem`` plus a
``com.canonical.dbusmenu`` menu, registered with
``org.kde.StatusNotifierWatcher``. That is all Gio, no extra dependency — the
full reasoning and sources are in ``research/tray-ubuntu-gtk4.md`` on branch
``research/tray-ubuntu-gtk4``.

Two behaviours the spec demands and hosts rely on:

* **degrade quietly** — with no watcher on the bus (GNOME with the extension
  disabled, or a bare session) there is simply no tray. Say so once, keep
  running, and appear later if a watcher shows up;
* **re-register** — GNOME Shell restarts take the watcher with them, so the name
  is watched and the item re-registered when it comes back.
"""
from __future__ import annotations

import os
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import tray_menu  # noqa: E402
from .tray_menu import MenuItem  # noqa: E402

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/StatusNotifierItem/menu"

# The app's own icons are installed by the .deb; until then fall back to a stock
# symbolic name so the tray always shows something recognisable.
_ICON_FALLBACKS = ("power-profile-performance-symbolic",
                   "preferences-system-symbolic")

_ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category"      type="s" access="read"/>
    <property name="Id"            type="s" access="read"/>
    <property name="Title"         type="s" access="read"/>
    <property name="Status"        type="s" access="read"/>
    <property name="IconName"      type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="Menu"          type="o" access="read"/>
    <property name="ItemIsMenu"    type="b" access="read"/>
    <property name="ToolTip"       type="(sa(iiay)ss)" access="read"/>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta"       type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <signal name="NewIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
  </interface>
</node>
"""

_MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version"       type="u"  access="read"/>
    <property name="Status"        type="s"  access="read"/>
    <property name="TextDirection" type="s"  access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId"       type="i"          direction="in"/>
      <arg name="recursionDepth" type="i"          direction="in"/>
      <arg name="propertyNames"  type="as"         direction="in"/>
      <arg name="revision"       type="u"          direction="out"/>
      <arg name="layout"         type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids"           type="ai"        direction="in"/>
      <arg name="propertyNames" type="as"        direction="in"/>
      <arg name="properties"    type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id"    type="i" direction="in"/>
      <arg name="name"  type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id"        type="i" direction="in"/>
      <arg name="eventId"   type="s" direction="in"/>
      <arg name="data"      type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events"   type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai"      direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id"         type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{sv})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent"   type="i"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg name="id"        type="i"/>
      <arg name="timestamp" type="u"/>
    </signal>
  </interface>
</node>
"""


def icon_candidates(app_id: str) -> tuple[str, ...]:
    """Icon names in preference order.

    The symbolic variant comes first: a tray lives in a monochrome panel strip,
    where the full-colour app tile reads as a dark blob and its blades shrink to
    a faint cross. Both are installed by the .deb; the stock names are there for
    a source checkout, where neither exists yet.
    """
    return (f"{app_id}-symbolic", app_id, *_ICON_FALLBACKS)


def pick_icon(app_id: str) -> str:
    """The best icon the theme actually has, else a stock symbolic that exists."""
    display = Gdk.Display.get_default()
    if display is not None:
        theme = Gtk.IconTheme.get_for_display(display)
        for name in icon_candidates(app_id):
            if theme.has_icon(name):
                return name
    return _ICON_FALLBACKS[-1]


class TrayIcon:
    """The indicator. ``on_action`` receives the ``tray_menu`` action strings."""

    def __init__(self, app_id: str, on_action: Callable[[str], None],
                 on_available: Callable[[bool], None] | None = None,
                 title: str = "GigaControl") -> None:
        self._app_id = app_id
        self._on_action = on_action
        self._on_available = on_available
        self._title = title
        self._icon = app_id
        self._items: list[MenuItem] = tray_menu.build(None, None, False)
        self._detail = "Starting…"
        self._revision = 1
        self._conn: Gio.DBusConnection | None = None
        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._name_id = 0
        self._watch_id = 0
        self._item_reg = 0
        self._menu_reg = 0
        self._warned = False
        self._stopped = False
        self.available = False  # a watcher is present and we are registered

    # --- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self._icon = pick_icon(self._app_id)
        Gio.bus_get(Gio.BusType.SESSION, None, self._on_bus)

    def _on_bus(self, _source, result) -> None:
        try:
            self._conn = Gio.bus_get_finish(result)
        except GLib.Error as exc:  # pragma: no cover - environment dependent
            print(f"gigactl-gui: no session bus, no tray: {exc}", flush=True)
            return
        try:
            self._item_reg = self._register(_ITEM_XML, ITEM_PATH,
                                           self._item_call, self._item_property)
            self._menu_reg = self._register(_MENU_XML, MENU_PATH,
                                           self._menu_call, self._menu_property)
        except Exception as exc:  # pragma: no cover - environment dependent
            print(f"gigactl-gui: could not export the tray objects: {exc}", flush=True)
            return
        # Own a name of the conventional shape, then hand it to the watcher.
        self._name_id = Gio.bus_own_name_on_connection(
            self._conn, self._bus_name, Gio.BusNameOwnerFlags.NONE, None, None)
        # Watching the watcher covers both "not there yet" and "GNOME Shell
        # restarted", which is the same code path either way.
        self._watch_id = Gio.bus_watch_name_on_connection(
            self._conn, WATCHER_NAME, Gio.BusNameWatcherFlags.NONE,
            self._on_watcher_appeared, self._on_watcher_vanished)

    def _register(self, xml: str, path: str, method_call, get_property) -> int:
        node = Gio.DBusNodeInfo.new_for_xml(xml)
        return self._conn.register_object(path, node.interfaces[0],
                                          method_call, get_property, None)

    def _on_watcher_appeared(self, conn, name: str, _owner: str) -> None:
        conn.call(name, WATCHER_PATH, WATCHER_NAME, "RegisterStatusNotifierItem",
                  GLib.Variant("(s)", (self._bus_name,)), None,
                  Gio.DBusCallFlags.NONE, -1, None, self._on_registered)

    def _on_registered(self, conn, result) -> None:
        try:
            conn.call_finish(result)
        except GLib.Error as exc:
            print(f"gigactl-gui: the tray host refused us: {exc.message}", flush=True)
            return
        if self._stopped:
            return  # the reply outlived us; do not come back to life
        self._set_available(True)
        print("gigactl-gui: tray icon registered", flush=True)

    def _on_watcher_vanished(self, _conn, _name: str) -> None:
        self._set_available(False)
        if not self._warned:
            self._warned = True
            print("gigactl-gui: no tray host on this session — running without a "
                  "tray icon. On GNOME, enable an AppIndicator extension.",
                  flush=True)

    def _set_available(self, available: bool) -> None:
        if available == self.available:
            return
        self.available = available
        if self._on_available:
            try:
                self._on_available(available)
            except Exception as exc:  # runs from a bus callback
                print(f"gigactl-gui: tray availability handler failed: {exc}",
                      flush=True)

    def stop(self) -> None:
        self._stopped = True
        for source, unwatch in ((self._watch_id, Gio.bus_unwatch_name),
                                (self._name_id, Gio.bus_unown_name)):
            if source:
                unwatch(source)
        self._watch_id = self._name_id = 0
        if self._conn:
            for reg in (self._item_reg, self._menu_reg):
                if reg:
                    self._conn.unregister_object(reg)
        self._item_reg = self._menu_reg = 0
        self._set_available(False)

    # --- state ---------------------------------------------------------------
    def update(self, active_profile: str | None, keyboard_on: bool | None,
               available: bool, detail: str | None = None) -> None:
        """Rebuild the menu from the daemon's state and tell hosts to re-read it."""
        self._items = tray_menu.build(active_profile, keyboard_on, available)
        if detail is not None:
            self._detail = detail
        self._revision += 1
        if self._conn is None:
            return
        try:
            self._conn.emit_signal(None, MENU_PATH, "com.canonical.dbusmenu",
                                   "LayoutUpdated",
                                   GLib.Variant("(ui)", (self._revision, 0)))
            self._conn.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem",
                                   "NewToolTip", None)
        except Exception as exc:
            print(f"gigactl-gui: could not refresh the tray: {exc}", flush=True)

    # --- StatusNotifierItem --------------------------------------------------
    def _item_property(self, _conn, _sender, _path, _iface,
                       prop: str) -> GLib.Variant | None:
        # Reached from the GLib dispatcher like the method calls, so it gets the
        # same guard: a raising property getter would take the loop down.
        try:
            return self._item_property_value(prop)
        except Exception as exc:
            print(f"gigactl-gui: tray property {prop} failed: {exc}", flush=True)
            return None

    def _item_property_value(self, prop: str) -> GLib.Variant | None:
        if prop == "Category":
            return GLib.Variant("s", "Hardware")
        if prop == "Id":
            return GLib.Variant("s", self._app_id)
        if prop == "Title":
            return GLib.Variant("s", self._title)
        if prop == "Status":
            return GLib.Variant("s", "Active")
        if prop == "IconName":
            return GLib.Variant("s", self._icon)
        if prop == "IconThemePath":
            return GLib.Variant("s", "")
        if prop == "Menu":
            return GLib.Variant("o", MENU_PATH)
        if prop == "ItemIsMenu":
            # False so hosts that honour Activate (KDE, XFCE) open the window on
            # a click; GNOME shows the menu either way, where "Open GigaControl"
            # is the first item.
            return GLib.Variant("b", False)
        if prop == "ToolTip":
            return GLib.Variant("(sa(iiay)ss)", (self._icon, [], self._title,
                                                 self._detail))
        return None

    def _item_call(self, _conn, _sender, _path, _iface, method: str,
                   _params, invocation) -> None:
        try:
            if method in ("Activate", "SecondaryActivate"):
                self._act(tray_menu.ACTION_OPEN)
            # ContextMenu/Scroll need no reply beyond an ack: the host draws the
            # menu itself from the dbusmenu object.
            invocation.return_value(None)
        except Exception as exc:  # never escape into the GLib dispatcher
            print(f"gigactl-gui: tray {method} failed: {exc}", flush=True)
            invocation.return_value(None)

    # --- com.canonical.dbusmenu ---------------------------------------------
    def _menu_property(self, _conn, _sender, _path, _iface,
                       prop: str) -> GLib.Variant | None:
        try:
            return self._menu_property_value(prop)
        except Exception as exc:
            print(f"gigactl-gui: tray menu property {prop} failed: {exc}", flush=True)
            return None

    def _menu_property_value(self, prop: str) -> GLib.Variant | None:
        if prop == "Version":
            return GLib.Variant("u", 3)
        if prop == "Status":
            return GLib.Variant("s", "normal")
        if prop == "TextDirection":
            return GLib.Variant("s", "ltr")
        if prop == "IconThemePath":
            return GLib.Variant("as", [])
        return None

    def _menu_call(self, _conn, _sender, _path, _iface, method: str,
                   params, invocation) -> None:
        try:
            if method == "GetLayout":
                invocation.return_value(GLib.Variant.new_tuple(
                    GLib.Variant("u", self._revision), self._layout()))
            elif method == "GetGroupProperties":
                ids, _names = params.unpack()
                invocation.return_value(GLib.Variant.new_tuple(
                    self._group_properties(ids)))
            elif method == "GetProperty":
                item_id, name = params.unpack()
                # the declared out arg is 'v', so the value has to be *boxed*:
                # new_tuple(value) would reply (s)/(b)/(i) and fail GDBus's check
                invocation.return_value(
                    GLib.Variant("(v)", (self._one_property(item_id, name),)))
            elif method == "Event":
                item_id, event_id, _data, _ts = params.unpack()
                if event_id == "clicked":
                    self._act(tray_menu.dispatch(self._items, item_id))
                invocation.return_value(None)
            elif method == "EventGroup":
                for item_id, event_id, _data, _ts in params.unpack()[0]:
                    if event_id == "clicked":
                        self._act(tray_menu.dispatch(self._items, item_id))
                invocation.return_value(GLib.Variant("(ai)", ([],)))
            elif method == "AboutToShow":
                # the menu is kept current as state changes, so never stale
                invocation.return_value(GLib.Variant("(b)", (False,)))
            else:
                invocation.return_value(None)
        except Exception as exc:  # never escape into the GLib dispatcher
            print(f"gigactl-gui: tray menu {method} failed: {exc}", flush=True)
            invocation.return_dbus_error("com.canonical.dbusmenu.Error", str(exc))

    def _act(self, action: str | None) -> None:
        if action:
            self._on_action(action)

    def _variant_props(self, item: MenuItem) -> dict[str, GLib.Variant]:
        return {name: GLib.Variant(signature, value)
                for name, (signature, value) in tray_menu.properties_of(item).items()}

    def _layout(self) -> GLib.Variant:
        """``(id, props, children)`` for the root, with every item as a child."""
        children = [GLib.Variant("(ia{sv}av)", (item.id, self._variant_props(item), []))
                    for item in self._items]
        root = {"children-display": GLib.Variant("s", "submenu")}
        return GLib.Variant("(ia{sv}av)", (0, root, children))

    def _group_properties(self, ids: list[int]) -> GLib.Variant:
        wanted = [i for i in self._items if not ids or i.id in ids]
        return GLib.Variant("a(ia{sv})",
                            [(i.id, self._variant_props(i)) for i in wanted])

    def _one_property(self, item_id: int, name: str) -> GLib.Variant:
        for item in self._items:
            if item.id == item_id:
                value = self._variant_props(item).get(name)
                if value is not None:
                    return value
        return GLib.Variant("s", "")
