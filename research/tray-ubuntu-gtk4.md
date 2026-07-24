# Tray/indicator support for a Python GTK4/libadwaita app on stock Ubuntu 24.04

Research for [issue #5](https://github.com/smairio/gigactl/issues/5). Researched 2026-07-24 against primary sources (GTK docs, Ubuntu archive/manifest, AyatanaIndicators upstream, freedesktop specs, systemd docs, real-world GTK4 app code).

## ANSWER / Recommendation

**Implement StatusNotifierItem (SNI) directly over D-Bus using Gio from `python3-gi` — do not use any appindicator library.**

GTK4 has no tray API at all (`GtkStatusIcon` was deprecated in 3.14 and never carried into GTK4), and `libayatana-appindicator` is a GTK3-linked library that cannot be loaded into a GTK4 process; upstream has closed GTK4 support as "not planned". Meanwhile, the thing that actually renders tray icons on stock Ubuntu 24.04 GNOME — the preinstalled, default-enabled `ubuntu-appindicators@ubuntu.com` shell extension — is a **StatusNotifierItem host**. The AppIndicator library was only ever a convenience wrapper around this same D-Bus protocol. So the reliable, dependency-free approach is to speak the protocol directly:

1. Export an object implementing **`org.kde.StatusNotifierItem`** at `/StatusNotifierItem` on the session bus (properties: `Category`, `Id`, `Title`, `IconName`, `Menu`, `ItemIsMenu`; methods: `Activate`, `SecondaryActivate`, `Scroll`; signals: `NewIcon`, `NewStatus`, ...).
2. Export a **`com.canonical.dbusmenu`** object at `/StatusNotifierItem/menu` for the context menu (GNOME's extension only shows a menu — design all actions as menu items).
3. Call `RegisterStatusNotifierItem` on **`org.kde.StatusNotifierWatcher`** (`/StatusNotifierWatcher`), and watch that bus name so you re-register whenever the watcher restarts (e.g. GNOME Shell reload).
4. Use a **themed icon name** (`IconName`) with the icon installed into `hicolor`, so it renders correctly across hosts and pixel densities.

This is exactly what mature Python GTK4 apps do today: Nicotine+ ships a self-contained ~500-line pure-`Gio` implementation ([`pynicotine/gtkgui/widgets/trayicon.py`](https://github.com/nicotine-plus/nicotine-plus/blob/master/pynicotine/gtkgui/widgets/trayicon.py), classes `StatusNotifierImplementation.StatusNotifierItemService` / `DBusMenuService`) — it needs nothing beyond PyGObject and works on GNOME, KDE, and XFCE. It is the recommended reference implementation to adapt for gigactl.

### .deb dependencies (Ubuntu 24.04)

| Relationship | Package | Why |
|---|---|---|
| `Depends` | `python3-gi` | `Gio`/`GLib` D-Bus — the only runtime the tray code needs |
| `Depends` | `gir1.2-gtk-4.0`, `gir1.2-adw-1` | the GTK4/libadwaita app itself (not the tray) |
| `Suggests` | `gnome-shell-extension-appindicator` | the SNI host on GNOME; **preinstalled on stock Ubuntu 24.04 desktop** (`58-1ubuntu24.04.1` in the ISO manifest), so a hard dependency is unnecessary — and `Recommends` would be wrong because the package depends on `gnome-shell` and would drag GNOME onto Kubuntu/Xubuntu systems |

No ayatana package is needed at all. (`libayatana-appindicator3-1` also happens to be preinstalled, but its GIR bindings `gir1.2-ayatanaappindicator3-0.1` are not, and depending on them buys nothing — see rejected alternatives.)

### Autostart

Ship an **XDG autostart desktop entry at `/etc/xdg/autostart/gigactl-tray.desktop`** (system-wide location per the freedesktop Autostart spec; this repo already targets system-wide install via `install.sh`). Do **not** hand-write a systemd user unit:

- The XDG spec is the cross-desktop convention: entries in `/etc/xdg/autostart/` are "automatically launched during startup of the user's desktop environment after the user has logged in", and a user can opt out per-app with a same-named file containing `Hidden=true` — behaviour a bare systemd unit doesn't give you.
- On systemd-managed sessions (Ubuntu GNOME is one), you get systemd anyway: `systemd-xdg-autostart-generator` "creates .service units for XDG autostart files", started via `xdg-desktop-autostart.target`, honoring `OnlyShowIn=`/`NotShowIn=` as `ExecCondition`s. So one `.desktop` file yields correct behaviour on every desktop, with systemd supervision for free on GNOME/KDE.
- A tray process should also wait for the watcher rather than assume it: on startup, if `org.kde.StatusNotifierWatcher` has no owner yet, subscribe to name-owner changes and register when it appears (the Nicotine+ reference does this).

### Fallbacks on other desktops

- **KDE Plasma:** works natively, zero extra packages — SNI *is* KDE's protocol (`org.kde.*` interfaces; the Ubuntu extension README calls KStatusNotifierItem "KDE's blessed successor of the systray").
- **XFCE:** works natively since Xfce 4.16 — the standalone `xfce4-statusnotifier-plugin` was merged into xfce4-panel's built-in "Status Tray" plugin as of 4.15 and is deprecated as a separate package; Xubuntu 24.04 ships Xfce 4.18.
- **GNOME without the extension** (e.g. stock Fedora, or a user who disabled it): no watcher exists → registration fails. Degrade gracefully: catch the failure, log a hint ("install/enable an AppIndicator extension"), keep running without a tray icon, and keep the D-Bus watch so the icon appears if the extension is enabled later.

---

## Evidence

### 1. GTK4 removed GtkStatusIcon and offers no replacement

- GTK3 reference, [`GtkStatusIcon`](https://docs.gtk.org/gtk3/class.StatusIcon.html): "GtkStatusIcon has been deprecated in 3.14. You should consider using notifications or more modern platform-specific APIs instead. GLib provides the `GNotification` API which works well with `GtkApplication` … and should be the preferred mechanism to notify the users of transient status updates." The class was dropped entirely in GTK4; the [GTK 3→4 migration guide](https://docs.gtk.org/gtk4/migrating-3to4.html) doesn't even mention a tray path, and `GNotification` is a notification, not a persistent tray presence. GTK4 apps that want a tray icon must go outside GTK.

### 2. libayatana-appindicator is GTK3-only and obsolete; GTK4 support was declined

- Upstream repo [AyatanaIndicators/libayatana-appindicator](https://github.com/AyatanaIndicators/libayatana-appindicator) is labeled "**OBSOLETE**, please use libayatana-appindicator-glib for new implementations", and describes itself as "based on the KSNI specification" (i.e. it is an SNI wrapper).
- Its Ubuntu 24.04 GIR package [`gir1.2-ayatanaappindicator3-0.1`](https://packages.ubuntu.com/noble/gir1.2-ayatanaappindicator3-0.1) ("Typelib files for libayatana-appindicator3-1 (**GTK-3+ version**)") depends on `gir1.2-gtk-3.0` — GTK3 and GTK4 cannot be loaded into the same process, so a Python app that has done `gi.require_version("Gtk", "4.0")` cannot import `AyatanaAppIndicator3`.
- The successor library's GTK4 request, [libayatana-appindicator-glib#22 "Introduce support for GTK 4"](https://github.com/AyatanaIndicators/libayatana-appindicator-glib/issues/22), is **closed as not planned** ("GtkMenu is removed in GTK 4, which complicates this process"). And `libayatana-appindicator-glib` is not packaged in Ubuntu 24.04 at all ([packages.ubuntu.com search](https://packages.ubuntu.com/search?keywords=libayatana-appindicator-glib&searchon=names&suite=noble&section=all): "your search gave no results").
- Real-world GTK4 projects hitting this and moving to direct SNI: [transmission#7364 "Migrate away from ayatana-indicators for GTK4 systray support"](https://github.com/transmission/transmission/issues/7364), [Tauon#1316 "Migrate from libayatana-appindicator … to org.kde.StatusNotifierItem"](https://github.com/Taiko2k/Tauon/issues/1316), [tauri-apps/libappindicator-rs#27 "Cannot use with GTK 4"](https://github.com/tauri-apps/libappindicator-rs/issues/27).

### 3. Stock Ubuntu 24.04 ships and enables an SNI host by default

- The Ubuntu 24.04.3 desktop ISO manifest ([releases.ubuntu.com/24.04/ubuntu-24.04.3-desktop-amd64.manifest](https://releases.ubuntu.com/24.04/ubuntu-24.04.3-desktop-amd64.manifest)) contains `gnome-shell-extension-appindicator 58-1ubuntu24.04.1` (plus `libayatana-appindicator3-1 0.5.93-1build3`) — verified by grepping the manifest.
- The extension ([ubuntu/gnome-shell-extension-appindicator](https://github.com/ubuntu/gnome-shell-extension-appindicator)): "This extension integrates Ubuntu AppIndicators and KStatusNotifierItems (KDE's blessed successor of the systray) into GNOME Shell. Including support for legacy tray icons." It implements `org.kde.StatusNotifierWatcher` and renders `org.kde.StatusNotifierItem` items — i.e. the protocol it expects **is** StatusNotifierItem; "AppIndicator" and "ayatana indicator" items are the same wire protocol.
- Ubuntu packaging renames it to uuid `ubuntu-appindicators@ubuntu.com` with description "Support app indicators and legacy tray icons in top panel, **as the default Ubuntu experience**" ([debian/patches/metadata-use-appindicator-namespace-and-naming.patch](https://git.launchpad.net/ubuntu/+source/gnome-shell-extension-appindicator/tree/debian/patches/metadata-use-appindicator-namespace-and-naming.patch?h=ubuntu/noble-updates)); it is enabled out of the box in the `ubuntu` session (it is one of the two system extensions of the default session, alongside the Dock).
- The package is in `main` ([packages.ubuntu.com/noble/gnome-shell-extension-appindicator](https://packages.ubuntu.com/noble/gnome-shell-extension-appindicator): "AppIndicator, KStatusNotifierItem and tray support for GNOME Shell").

### 4. Reference implementation in Python (GTK4-compatible, PyGObject-only)

- Nicotine+ [`trayicon.py`](https://github.com/nicotine-plus/nicotine-plus/blob/master/pynicotine/gtkgui/widgets/trayicon.py): `StatusNotifierItemService` exports `org.kde.StatusNotifierItem` at `/StatusNotifierItem`, `DBusMenuService` exports `com.canonical.dbusmenu` at `/StatusNotifierItem/menu`, registration via `RegisterStatusNotifierItem` on `org.kde.StatusNotifierWatcher`, all with `Gio.DBusConnection.register_object` — no GTK3, no external tray library, works from a GTK4 app. It also watches the watcher name (`Gio.bus_watch_name`) to survive shell restarts and raises a clean `ImplementationUnavailable` when no watcher exists.
- The protocol specs: [StatusNotifierItem](https://www.freedesktop.org/wiki/Specifications/StatusNotifierItem/) (freedesktop, authored by KDE) and [com.canonical.dbusmenu](https://github.com/AyatanaIndicators/libdbusmenu) (the menu protocol the GNOME extension and Plasma both consume).

### 5. Autostart convention

- [freedesktop Autostart spec](https://specifications.freedesktop.org/autostart-spec/latest/): system-wide autostart directory is `/etc/xdg/autostart/` (from `$XDG_CONFIG_DIRS`), per-user is `~/.config/autostart/`; "the application will be automatically launched during startup of the user's desktop environment after the user has logged in", and a user can disable a system entry via a same-named `.desktop` with `Hidden=true`.
- [`systemd-xdg-autostart-generator(8)`](https://man7.org/linux/man-pages/man8/systemd-xdg-autostart-generator.8.html): "creates .service units for XDG autostart files"; units are started by the desktop environment via `xdg-desktop-autostart.target`, with `OnlyShowIn=`/`NotShowIn=` translated into `ExecCondition`s. Conclusion: shipping the XDG entry *is* the systemd-integrated path on Ubuntu GNOME; a hand-rolled user unit duplicates this less portably.

### 6. Other desktops

- **KDE Plasma:** SNI is the native Plasma system-tray protocol (all interfaces live under `org.kde.*`); no extra packages.
- **XFCE:** [xfce4-statusnotifier-plugin docs](https://docs.xfce.org/panel-plugins/xfce4-statusnotifier-plugin/start) / [mirror README](https://github.com/xfce-mirror/xfce4-statusnotifier-plugin): "As of xfce 4.15, the functionality provided by xfce4-statusnotifier-plugin has been integrated into the xfce4-panel's systray" (now the "Status Tray" plugin handling both XEmbed and SNI). Xubuntu 24.04 ships Xfce 4.18.

---

## Rejected alternatives

| Alternative | Why rejected |
|---|---|
| **`AyatanaAppIndicator3` via GIR in-process** | Links GTK3 (`gir1.2-ayatanaappindicator3-0.1` → `gir1.2-gtk-3.0`); cannot coexist with GTK4 in one Python process — `gi` refuses to load both. Library is upstream-OBSOLETE; GTK4 port declined ([issue #22](https://github.com/AyatanaIndicators/libayatana-appindicator-glib/issues/22)). |
| **Separate GTK3 helper process using AppIndicator3** | Functionally works (spawn a small GTK3 Python process, talk to it over a pipe/D-Bus), but adds a dependency that is *not* preinstalled (`gir1.2-ayatanaappindicator3-0.1`), a second process to babysit, and custom IPC — all to end up emitting the very same SNI D-Bus traffic the app can emit itself with `Gio`. Only worth it if the direct implementation proved unmaintainable; Nicotine+ shows it isn't. |
| **pystray** (`python3-pystray` 0.19.4, universe) | Its Linux backends are: `appindicator` (loads GTK3 AppIndicator3 → same in-process conflict), `gtk` (GtkStatusIcon/XEmbed) and `xorg` (XEmbed) — XEmbed is legacy-only on GNOME Wayland and deprecated in the Ubuntu extension; the useful backend re-imports GTK3. Also universe rather than main, and an abstraction that hides the menu model SNI needs. |
| **ksni** | Rust crate implementing SNI — right idea, wrong language for a Python app; no maintained Python binding. |
| **`Gio.Notification` only** | What GTK upstream nominally recommends, but it is a transient notification API, not a persistent clickable tray presence with a menu — doesn't satisfy the requirement. |
| **Wait for / vendor `libayatana-appindicator-glib`** | Not packaged in noble at all; its GTK4 issue closed "not planned". |
| **systemd user unit for autostart** | Loses the cross-desktop convention (`/etc/xdg/autostart` works on GNOME/KDE/XFCE/others), loses per-user `Hidden=true` override and `OnlyShowIn` gating, and must hand-solve ordering against the graphical session — which `systemd-xdg-autostart-generator` already solves for XDG entries. Reasonable only for non-desktop daemons (gigactl's root-side fan daemon should stay a systemd *system* unit; this recommendation concerns only the per-user tray UI). |

## Implementation notes for gigactl

- Put the tray in the GUI/user process, not the root daemon; talk to the daemon the same way the CLI does.
- Menu-first UX: on GNOME the extension exposes only the dbusmenu (left/right click both open it; `Activate` fires on double-click), so every action (fan profile switch, open app, quit) must be a menu item.
- `Status`/`IconName` updates via the `NewStatus`/`NewIcon` signals are cheap — usable for reflecting the active fan profile.
- Degrade gracefully when no `org.kde.StatusNotifierWatcher` owner exists (GNOME with extension disabled): log once, keep the app alive, re-register on name-appear.
