#!/usr/bin/env python3
"""Check the keyboard backlight: master enable, one colour, brightness.

    sudo python3 tools/keyboard_check.py

Sets the single-zone backlight to orange at full brightness for ~6s so you
can see it, then puts the colour AND the brightness back (finally block, so
Ctrl-C does too). The EC cannot be asked what the backlight showed before —
its mirror fields read back stale (docs/PROTOCOL.md) — so "back" means what
the daemon itself re-applies when it starts: the backlight it saved, or the
firmware default (blue, full brightness) if it saved none. The EC forgets
LED state at reboot regardless.
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "daemon"))

import guard  # noqa: E402
from gigactld import keyboard, state  # noqa: E402
from gigactld.ec import EmbeddedController  # noqa: E402

TEST_COLOR = (255, 102, 0)  # orange — unmistakably not the factory blue
SHOW_FOR_S = 6
# The backlight the EC boots with: the factory blue at full brightness.
FIRMWARE_DEFAULT = keyboard.KeyboardState(enabled=True, r=0, g=0, b=200,
                                          brightness_pct=100)


def restore_target(state_path: str = state.DEFAULT_PATH) -> keyboard.KeyboardState:
    """The backlight the daemon re-applies at start, or the firmware default."""
    saved = (state.load(state_path) or {}).get("keyboard")
    return keyboard.KeyboardState.from_dict(saved) if saved else FIRMWARE_DEFAULT


def describe(kbd: keyboard.KeyboardState) -> str:
    if not kbd.enabled:
        return "off"
    return f"R={kbd.r} G={kbd.g} B={kbd.b} at {kbd.brightness_pct}%"


def check(ec, restore_to: keyboard.KeyboardState, pause=time.sleep) -> None:
    print(">>> enabling keyboard control, setting ORANGE @100% — look at the keys <<<", flush=True)
    try:
        keyboard.set_enabled(ec, True)
        keyboard.set_color(ec, *TEST_COLOR)
        keyboard.set_brightness(ec, 100)
        pause(SHOW_FOR_S)
    finally:
        # Colour and brightness both: the test changed both.
        restore_to.apply(ec)
        print(f">>> keyboard put back: {describe(restore_to)} <<<", flush=True)


def main() -> int:
    # The daemon's own backend choice: ec_sys if loaded, /dev/port only if
    # not. Creating it touches no hardware; the checks come first.
    ec = EmbeddedController()
    reason = guard.refusal() or ec.unwritable_reason()
    if reason:
        print(f"keyboard_check: {reason}", file=sys.stderr)
        return 1
    check(ec, restore_target())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
