# gigactl-gui — GigaControl desktop GUI

The unprivileged GTK4 / libadwaita front end. It classifies the machine from DMI
and talks to the root daemon over the D-Bus **system** bus; it never touches the
EC directly (all hardware access lives in `../daemon`).

## Run (development)

```sh
# needs the daemon running (see ../daemon), plus python3-gi + GTK4/libadwaita
# introspection (gir1.2-gtk-4.0, gir1.2-adw-1)
PYTHONPATH=. python3 -m gigactl_gui           # open the window
PYTHONPATH=. python3 -m gigactl_gui --tray    # start hidden in the tray
```

Launches the **Overview**: a model-verification banner, a live hero (CPU/GPU
semantic ring gauges, a status pill, per-fan RPM + duty) driven by the daemon's
`Telemetry` signal, the fan-profile row, and the keyboard light section with its
live preview. `Custom…` opens the second screen, the **curve editor**. Light/dark
follow the system automatically (libadwaita).

## Layout

- `model.py` — DMI → verified / expected / unsupported (mirrors the `gfan`/`gkbd` guard).
- `temperature.py` — the one semantic colour scale (cool/warm/hot) + gauge math.
- `backlight.py` — how brightly the keyboard preview should glow.
- `palette.py` — keyboard swatches, drawn in the exact RGB they send.
- `profiles.py` — the profile row's metadata; names `custom`/`manual` honestly.
- `curves.py` — curve maths: prediction (incl. the daemon's floor), monotonic
  dragging, and the graph↔value mapping.
- `errors.py` — D-Bus error → one short plain-English sentence.
- `client.py` — system-bus client: `Telemetry`, property read-back, control calls.
- `style.py` — the two theme queries the Cairo widgets share (dark mode, accent).
- `gauge.py` / `preview.py` / `curve_view.py` — the Cairo widgets (temperature
  ring, keyboard glow, draggable curve plot).
- `curve_page.py` — screen two: Linked⇄Split, live annotation, floor note,
  Firmware-auto escape.
- `window.py` / `app.py` / `__main__.py` — the Overview, the navigation host, the
  app and entry point.

## Editing the curve without a mouse

The graph is focusable. With focus on it: `←`/`→` move the selected point's
temperature, `↑`/`↓` its speed (Shift for bigger steps), `Page Up`/`Page Down`
pick a point, `Home`/`End` jump to the first/last. The accessible label always
names the selected point and its values, so the shape is legible to a screen
reader — DESIGN.md requires every control to be reachable this way, and a graph
is the one control where mouse-only would lock people out entirely.

## Why the curve maths is duplicated

`curves.py` re-implements the daemon's piecewise-linear lookup, its 30 %/85 °C
safety floor and its monotonic rules. That is deliberate: the two packages
install separately, so this is a wire contract rather than shared code. It exists
so the editor cannot show a shape or a predicted duty that the daemon would
quietly rewrite — change the two together.

## How the controls stay honest

Every control shows the **daemon's** state, not its own. A click is echoed
locally for instant feedback, then confirmed (or corrected) by the
`PropertiesChanged` the daemon broadcasts — so a refused write surfaces as a
toast *and* snaps the control back. Two traps worth knowing if you extend this:
programmatically moving a widget fires the same signal a click does (hence the
`_syncing` guard), and Gio clears the proxy's property cache when the daemon
exits (hence `_last_profile`).

## Tests

```sh
python3 -m pytest -q   # pure logic: model, temperature, palette, profiles,
                       # error wording, telemetry/keyboard parsing, glow
```

The GTK widgets are verified by driving the real handlers against a live daemon
and real hardware, not unit-tested.
