# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

*(Design surface only: mockups/prototypes are built and iterated as HTML. The shipped product is a **Linux desktop app** — Python + GTK4/libadwaita on Ubuntu, following GNOME HIG. Every design decision must be translatable to GTK4 widgets; see Capabilities and Constraints.)*

## Users

Any Gigabyte G5/G6 laptop owner running Linux — **including beginners** who just installed Ubuntu and want their loud, hot laptop fixed. No terminal knowledge can be assumed: the app must be obvious at first launch with zero learning curve. Technical users exist as a secondary audience and are served by depth (curves, exact values), never at the cost of the beginner's first five minutes.

## Product Purpose

gigactl is the missing control software for Gigabyte G5/G6 gaming laptops on Linux. Gigabyte ships Windows-only tooling (Gigabyte Control Center); on Linux these laptops run their firmware's lazy fan curve (idling ~90 °C, throttling at ~96 °C) with no way to change fan speeds or keyboard lighting. gigactl's promise: **a full control replacement for GCC** — fans (manual + automatic temperature curves), keyboard RGB, and live monitoring — with nothing missing that would send a user back to Windows.

Success: a G5/G6 owner installs one .deb, sees their temps drop, sets their keyboard color, and never thinks about the tool again — it autostarts in the tray and just works.

## Positioning

The only control app for these machines on Linux — built on a first-of-its-kind reverse-engineered EC protocol (documented in `docs/PROTOCOL.md`). Generic tools (NBFC, fancontrol, OpenRGB) cannot support this hardware at all; tuxedo-drivers refuses non-TUXEDO branding. Nobody else can truthfully claim "your Gigabyte gaming laptop, fully working on Linux."

## Operating Context

- Runs on Ubuntu 24.04+ (stock GNOME primarily; KDE/XFCE secondary), installed from a .deb on GitHub Releases.
- Lives in the system tray (StatusNotifierItem), autostarted at login, window opened on demand.
- Architecture (decided): unprivileged GTK4 GUI ↔ D-Bus system bus ↔ root daemon that owns all EC access and runs the fan-curve engine.
- The existing CLI tools `gfan`/`gkbd` in this repo are the reference implementation of every hardware capability.
- Work is planned on the wayfinder map (GitHub issue #1); open design-adjacent decisions live in tickets.

## Capabilities and Constraints

Confirmed hardware/product capabilities (all proven live on a G6 KF):

- Fans: per-fan duty 0–100 % (2 fans: CPU/GPU), instant handback to firmware-auto, live RPM + CPU/GPU temperatures.
- Fan curves (v1 scope, confirmed): automatic temperature→duty curves executed by a background service. Curve data model **not yet decided** (map ticket #8).
- Keyboard: single-zone RGB color + brightness 0–255 + on/off. Not per-key, not multi-zone.
- Safety invariants the UI must surface, never hide: DMI model guard (Gigabyte G5/G6/G7 only), write-verification with auto-revert, firmware-auto as the failsafe state, reboot always resets the EC.

Constraints:

- Final UI is GTK4/libadwaita — no web-only affordances in the shipped design.
- EC accepts one writer at a time; all control flows through the daemon.
- Verified on G6 KF only; sibling models are "expected to work" — the UI must be honest about that distinction.

## Brand Commitments

- Repo/package name: **gigactl** (github.com/smairio/gigactl). Display name, app-id, and icon are **explicitly undecided** (map ticket #10).
- Voice: plain, short, simple English readable by non-native speakers (confirmed). No jargon in primary flows; technical terms allowed in advanced/curve views.

## Evidence on Hand

- Working CLI tools: `gfan`, `gkbd` (this repo) — every capability demonstrable today.
- Reverse-engineered protocol documentation: `docs/PROTOCOL.md`.
- Research (accepted onto the map): tray/StatusNotifierItem approach (`research/tray-ubuntu-gtk4` branch), .deb/daemon/polkit packaging (`research/deb-packaging` branch).
- Real measurements for honest claims: stock firmware idles ~90 °C holding 27 % fan duty; manual 70 % duty ≈ 5,000 RPM with immediate temperature drop.
- No testimonials, user counts, or third-party reviews exist — never fabricate any.

## Product Principles

1. **Beginner-first, depth behind one click.** The first screen solves heat/noise/color without reading anything; curves and telemetry live one level deeper.
2. **Never trap the hardware.** Every state is escapable to firmware-auto in one action; failsafes are visible, not buried.
3. **Honest about hardware.** Show what's verified vs expected-to-work; never pretend capabilities (zones, sensors) the machine lacks.
4. **Full GCC parity is the bar.** A user should never need Windows for fans, lighting, or monitoring again.
5. **Simple words.** Short English sentences a non-native speaker parses at a glance.

## Accessibility & Inclusion

Primary confirmed need: language simplicity (non-native English readers). Standard GNOME accessibility expectations apply to the GTK implementation (keyboard navigation, screen-reader labels via ATK); no additional product-specific requirement established.
