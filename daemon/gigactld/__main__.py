"""Entry point: `gigactld` / `python3 -m gigactld`."""
from __future__ import annotations

import sys

from . import fans
from .ec import EmbeddedController, EcError
from .service import Daemon


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    interval = 2
    if "--interval" in argv:
        interval = int(argv[argv.index("--interval") + 1])

    try:
        ec = EmbeddedController()
    except EcError as exc:
        print(f"gigactld: cannot open the EC: {exc}", file=sys.stderr)
        return 1

    # One-shot used by the unit's ExecStopPost: hand fans back to firmware even
    # if the daemon crashed (systemd runs this after the main process exits).
    if "--restore-firmware" in argv:
        for f in fans.FANS:
            fans.restore_auto(ec, f)
        return 0

    # SIGINT/SIGTERM are handled inside Daemon.run via GLib.unix_signal_add.
    Daemon(ec, interval_s=interval).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
