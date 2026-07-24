"""Turn a D-Bus error into one short sentence a beginner can act on.

Raw GLib errors read like
``GDBus.Error:io.github.smairio.gigactl.Error.WriteRejected: fan(s) [1] did not
accept the duty; all reverted to firmware auto`` — useful to us, noise to the
user. PRODUCT.md #5 asks for short plain English, so we map the errors the
daemon actually raises and fall back to the detail text for anything unknown
(showing the real reason beats hiding it behind a generic apology).
"""
from __future__ import annotations

_GENERIC = "That did not work."

# The one place this sentence is written; the client reports it directly when it
# can see there is no daemon to call in the first place.
NO_DAEMON = "The gigactl daemon is not running."

# Matched against the error *name*, longest-specific first.
_MESSAGES: tuple[tuple[str, str], ...] = (
    ("WriteRejected",
     "The fans refused that speed, so they are back on firmware auto."),
    ("NotAuthorized",
     "You are not allowed to change this. Log in as an administrator."),
    ("InvalidArgs",
     "That value is out of range."),
    ("ServiceUnknown", NO_DAEMON),
    ("NameHasNoOwner", NO_DAEMON),
    ("NoReply",
     "The gigactl daemon did not answer. Try again."),
    ("UnknownMethod",
     "This version of the gigactl daemon is too old for that."),
)


def _split(raw: str) -> tuple[str, str]:
    """(error name, detail) from a raw GLib error string."""
    body = raw[len("GDBus.Error:"):] if raw.startswith("GDBus.Error:") else raw
    name, _, detail = body.partition(":")
    return name.strip(), detail.strip()


def human_message(raw: str) -> str:
    name, detail = _split(raw or "")
    for needle, message in _MESSAGES:
        if needle in name:
            return message
    return detail or _GENERIC
