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
    # The flags are ours, not GTK's; hand it only the program name so it does not
    # reject them.
    return GigactlApp(start_hidden=tray).run([argv[0]] if argv else [])


if __name__ == "__main__":
    raise SystemExit(main())
