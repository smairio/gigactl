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

Launches the **Overview**: a model-verification banner + a live hero (CPU/GPU
semantic ring gauges, a status pill, and per-fan RPM + duty) driven by the
daemon's `Telemetry` signal. Read-only for now — profile controls, the keyboard
section, and the custom-curve screen arrive in later tickets. Light/dark follow
the system automatically (libadwaita).

## Layout

- `model.py` — DMI → verified / expected / unsupported (mirrors the `gfan`/`gkbd` guard).
- `temperature.py` — the one semantic colour scale (cool/warm/hot) + gauge math.
- `client.py` — system-bus client: `Telemetry` signal, name-owner tracking.
- `gauge.py` — the Cairo temperature ring widget.
- `window.py` / `app.py` / `__main__.py` — the Overview window, app, entry point.

## Tests

```sh
python3 -m pytest -q      # pure logic: model, temperature scale, telemetry parsing
```

The GTK widgets (gauge/window/app) are verified by launching against a live
daemon, not unit-tested.
