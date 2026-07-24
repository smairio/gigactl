"""Entry point: `gigactl-gui` / `python3 -m gigactl_gui`."""
from __future__ import annotations

import sys

from .app import GigactlApp

_USAGE = """gigactl-gui — GigaControl, fan and keyboard control for Gigabyte G5/G6

Usage:
  gigactl-gui            open the window
  gigactl-gui --tray     start hidden in the tray (used by the autostart entry)
  gigactl-gui --help     this message
"""


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    if "--help" in argv or "-h" in argv:
        print(_USAGE, end="")
        return 0
    tray = "--tray" in argv
    app = GigactlApp(start_hidden=tray)
    if tray and _already_running(app):
        # Autostart racing a manual launch: activating the primary instance would
        # pop its window, which is the opposite of what --tray asked for.
        print("gigactl-gui: already running; leaving it as it is", flush=True)
        return 0
    # The flags are ours, not GTK's; hand it only the program name so it does not
    # reject them.
    return app.run([argv[0]] if argv else [])


def _already_running(app: GigactlApp) -> bool:
    """True when another instance already owns the app id. Imported here because
    the module header should not have to do the gi version dance for one call."""
    from gi.repository import GLib

    try:
        app.register(None)
    except GLib.Error:
        return False  # could not ask; carry on and let run() sort it out
    return app.get_is_remote()


if __name__ == "__main__":
    raise SystemExit(main())
