"""Platform styling queries the Cairo widgets share.

Small on purpose: these are the two things a custom-drawn widget needs from the
theme, and having them in one place stops each widget from asking in its own
slightly different way.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


def is_dark() -> bool:
    return Adw.StyleManager.get_default().get_dark()


def accent_rgb(widget: Gtk.Widget) -> tuple[float, float, float]:
    """The platform accent colour, so custom plots belong to the user's theme.

    Read from the widget's own CSS colour — give it the ``.curve-accent`` class,
    which resolves to ``@accent_color`` — rather than a style-context lookup,
    which GTK 4.10 deprecated.
    """
    rgba = widget.get_color()
    return (rgba.red, rgba.green, rgba.blue)
