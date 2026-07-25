"""GigaControl — the GTK4/libadwaita desktop GUI for gigactl.

Unprivileged front end: it reads DMI to classify the model and talks to the
root daemon over the D-Bus **system** bus (telemetry + control). All EC access
lives in the daemon; this package never touches hardware directly.
"""

__version__ = "1.0.1"
