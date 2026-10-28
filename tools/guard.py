"""May this tool write the EC right now?

Every tool here that writes the EC asks ``refusal()`` first and stops if it
gets a sentence back. The two rules are the ones gfan and gkbd already follow
(gigactl-cli-lib.sh):

* **The daemon is the only writer.** A tool runs only when the bus says,
  definitely, that the gigactl daemon is not running. If it cannot tell (no
  busctl, no answer, an answer it does not understand), it refuses rather than
  guess. Writing beside a live daemon is how a fan ends up stuck at someone
  else's duty, or how the daemon's curve quietly undoes the test.
* **Gigabyte G5/G6/G7 only.** The same DMI patterns as gfan's model_check.
  ``GIGACTL_TOOLS_UNSAFE=1`` skips this check, and only this one.

The command runner, environment and DMI paths are parameters so that
``tools/test_guard.py`` can test all of this with no bus and no hardware.
"""
from __future__ import annotations

import os
import subprocess

BUS_NAME = "io.github.smairio.gigactl"
# The exact question detect_mode asks in gigactl-cli-lib.sh.
NAME_HAS_OWNER = ["busctl", "--system", "call", "org.freedesktop.DBus",
                  "/org/freedesktop/DBus", "org.freedesktop.DBus",
                  "NameHasOwner", "s", BUS_NAME]

VENDOR_PATH = "/sys/class/dmi/id/sys_vendor"
MODEL_PATH = "/sys/class/dmi/id/product_name"
UNSAFE_ENV = "GIGACTL_TOOLS_UNSAFE"

DAEMON_RUNNING = (
    "the gigactl daemon is running, and it must be the only program that "
    "writes the EC. Stop it first with 'sudo systemctl stop gigactl', and "
    "start it again when you are done with 'sudo systemctl start gigactl'.")

NO_BUSCTL = (
    "busctl not found, so there is no way to tell whether the gigactl daemon "
    "is running — refusing to touch the EC behind its back. (busctl ships "
    "with systemd.)")


def _cannot_tell(detail: str) -> str:
    return (f"cannot tell whether the gigactl daemon is running ({detail}) — "
            "refusing to touch the EC behind its back.")


def daemon_refusal(run=subprocess.run) -> str | None:
    """None only when the bus answers a definite "b false" for the daemon's
    name; otherwise the reason to refuse. ``run`` is ``subprocess.run`` or a
    stand-in with the same signature."""
    try:
        # stderr joins stdout, as `2>&1` does in detect_mode: a warning next
        # to the answer makes it something other than a definite "b false".
        proc = run(NAME_HAS_OWNER, stdout=subprocess.PIPE,
                   stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError:
        return NO_BUSCTL
    answer = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return _cannot_tell(answer or f"busctl exited with status {proc.returncode}")
    if answer == "b false":
        return None
    if answer == "b true":
        return DAEMON_RUNNING
    return _cannot_tell(f"busctl answered: {answer}")


def model_refusal(vendor: str, model: str) -> str | None:
    """None for a Gigabyte G5/G6/G7, otherwise the reason to refuse.

    The same patterns as gfan's model_check: vendor GIGABYTE*|Gigabyte*, model
    G5*|G6*|G7*. Unreadable DMI arrives here as "" and is refused.
    """
    if not vendor.startswith(("GIGABYTE", "Gigabyte")):
        return (f"vendor '{vendor}' is not Gigabyte — these tools are for "
                f"Gigabyte G5/G6/G7 laptops only. {UNSAFE_ENV}=1 to override "
                "(risky).")
    if not model.startswith(("G5", "G6", "G7")):
        return (f"model '{model}' is not a Gigabyte G5/G6/G7 — these tools are "
                f"for that family only. {UNSAFE_ENV}=1 to override (risky).")
    return None


def _dmi(path: str) -> str:
    """A DMI string as gfan reads it: `$(cat path 2>/dev/null || true)`."""
    try:
        with open(path) as f:
            return f.read().rstrip("\n")
    except OSError:
        return ""


def refusal(*, run=subprocess.run, environ=os.environ,
            vendor_path: str = VENDOR_PATH,
            model_path: str = MODEL_PATH) -> str | None:
    """Why this tool must not write the EC now, or None if it may.

    The machine is checked first: on the wrong hardware, how to stop the
    daemon is not the useful thing to say.
    """
    if environ.get(UNSAFE_ENV) != "1":
        reason = model_refusal(_dmi(vendor_path), _dmi(model_path))
        if reason:
            return reason
    return daemon_refusal(run)
