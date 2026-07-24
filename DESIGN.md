# Design

<!-- impeccable:design-schema 1 -->

The visual and interaction authority for **gigactl Desktop**. Reference mockup: [design/main-window.html](design/main-window.html) (light/dark, two screens). The app ships in **GTK4 + libadwaita**; this document is the source of truth the native implementation follows. Losing prototype variants live on branch `design/main-window-prototype`.

## Visual world

Native GNOME / libadwaita — the app must look like it belongs in Ubuntu, not like a themed web app. Brand is expressed through **precise, meaningful detail**, never chrome:

- **Temperature is the color system.** One semantic scale drives every temperature readout: cool `#26a269` (< 75 °C) → warm `#e5a50a` (75–89 °C) → hot `#e01b24` (≥ 90 °C). Dark-mode variants lighten for contrast. This scale is the app's signature — it is never used decoratively for anything that is not a temperature.
- **Accent** is libadwaita blue `#3584e4` (light) / `#78aeed` (dark), used for the active profile and interactive fills only.
- **The keyboard preview glows in the real selected color** — a literal depiction of the physical backlight, the one place a colored glow is correct.

Use the platform's own tokens when implementing in GTK4 (`@accent_color`, `@card_bg_color`, `@window_bg_color`, etc.) rather than hardcoding these hex values; the hex above defines intent for the mockup and for any non-GTK surface (web, screenshots).

## Type & spacing

- System UI font (Adwaita Sans / Cantarell on GNOME; Inter as the mockup stand-in). No decorative or monospace fonts; digits use tabular figures for stable readouts.
- Scale: hero temperature ~32 px/800; section titles 16 px/800; row labels 13.5 px/600; metadata 11–12 px. Eyebrows are 11 px, uppercase, tracked `.08em`, in `--ink-3` — used sparingly (fan labels, curve axis title), not over every block.
- Elevation via neutral shadows with real offset + blur (`--shadow`, `--shadow-sm`); no zero-offset halos anywhere except the keyboard glow.
- Corners: window 13 px, cards/controls 9–11 px.

## Structure (chosen layout)

Single resizable window, **two screens**, no persistent tab bar:

**1. Overview (default)** — top-to-bottom, beginner-first:
1. **Model-verification banner** — green "detected · fully verified" for G6 KF; amber "expected to work" for other G5/G6; red "unsupported" blocks controls. Honesty is non-negotiable (PRODUCT.md principle 3).
2. **Live-state hero** — CPU + GPU semantic ring gauges (big tabular number) beside a status pill and per-fan RPM + duty. Answers "is my laptop OK?" at a glance.
3. **Fan profile row** — one-click segments: Quiet · Balanced · Performance · Max · Firmware · Custom…. Active profile filled with accent (Firmware fills neutral gray — it is the yield-to-hardware state). `Custom…` opens screen 2.
4. **Keyboard light** — color swatches (+ custom wheel), brightness slider, and the live glowing preview.

**2. Custom curve** — reached from `Custom…`, returns via "‹ Back to overview":
- Large temp→fan-speed graph, editable points (drag), Linked ⇄ Split segmented toggle.
- Live annotation ("CPU now 91° → fans 78%"), the 30 %-floor-above-85 ° safety note, and a persistent **Firmware auto** escape button.

## Interaction & states

- Profile click → apply immediately → hero reflects within one poll (~2 s). Curve edits apply live (debounced). Keyboard color/brightness apply instantly to hardware and preview.
- **Every control screen keeps a visible Firmware-auto escape** (PRODUCT.md principle 2). Firmware is a first-class state shown as *controls yielded*, never an error.
- Required states to design in the native build: daemon unreachable (start/install CTA), unsupported model (explain, disable writes), unverified-but-family model (amber banner, allow), EC write refused → auto-reverted (inline notice), curve applying, first-run/empty.

## Localization & a11y

- Simple, short English (confirmed v1). Strings externalized for later translation; avoid idioms.
- GTK/ATK: every control keyboard-reachable and screen-reader-labelled; the temperature scale is never the *only* signal — pair color with the number and, where used, an arrow/word.

## Anti-goals

- No gamer-RGB aesthetic, no dark-by-default "cool" look (theme follows the system).
- No decorative gradients, glass, sparklines, or metric-tile filler; graphs appear only where they carry real data (the curve).
- No web-only affordances that cannot map to a GTK4 widget.
