"""Entry point: `gigactl-gui` / `python3 -m gigactl_gui`."""
from __future__ import annotations

import sys

from .app import GigactlApp


def main(argv: list[str] | None = None) -> int:
    return GigactlApp().run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
