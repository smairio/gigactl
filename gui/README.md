# gigactl-gui — GigaControl desktop GUI

The unprivileged GTK4 / libadwaita front end. It classifies the machine from DMI
and talks to the root daemon over the D-Bus **system** bus; it never touches the
EC directly (all hardware access lives in `../daemon`).

## Run (development)

```sh
# needs the daemon running (see ../daemon), plus python3-gi + GTK4/libadwaita
# introspection (gir1.2-gtk-4.0, gir1.2-adw-1)
PYTHONPATH=. python3 -m gigactl_gui
```

Launches the **Overview**: a model-verification banner, a live hero (CPU/GPU
semantic ring gauges, a status pill, per-fan RPM + duty) driven by the daemon's
`Telemetry` signal, the fan-profile row, and the keyboard light section with its
live preview. The custom-curve screen arrives in a later ticket. Light/dark
follow the system automatically (libadwaita).

## Layout

- `model.py` — DMI → verified / expected / unsupported (mirrors the `gfan`/`gkbd` guard).
- `temperature.py` — the one semantic colour scale (cool/warm/hot) + gauge math.
- `palette.py` — keyboard swatches, drawn in the exact RGB they send.
- `profiles.py` — the profile row's metadata; names `custom`/`manual` honestly.
- `errors.py` — D-Bus error → one short plain-English sentence.
- `client.py` — system-bus client: `Telemetry`, property read-back, control calls.
- `gauge.py` / `preview.py` — the Cairo temperature ring and keyboard glow.
- `window.py` / `app.py` / `__main__.py` — the Overview window, app, entry point.

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
