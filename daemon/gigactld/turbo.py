"""Intel Turbo Boost switch, via intel_pstate's ``no_turbo`` sysfs knob.

Turbo lets the CPU run above its base speed for short bursts. Turning it off
trades burst speed for less heat and fan noise, which is why it sits next to the
fan controls. Every Gigabyte G5/G6 in scope is Intel; on a machine without the
intel_pstate driver the knob is simply absent and the switch is not offered.

The knob has other writers: power-profiles-daemon turns turbo off in Power
Saver, and tuned/TLP can do the same. So the daemon reports the *live* value,
announces changes it did not make, and re-applies a choice only when the user
made one through it (at start, and when they toggle) — never on a timer.
"""
from __future__ import annotations

NO_TURBO_PATH = "/sys/devices/system/cpu/intel_pstate/no_turbo"


class TurboUnavailable(Exception):
    """The knob does not exist (not intel_pstate, or HWP hides it)."""


class TurboControl:
    def __init__(self, path: str = NO_TURBO_PATH) -> None:
        self.path = path

    def available(self) -> bool:
        try:
            with open(self.path) as f:
                return f.read().strip() in ("0", "1")
        except OSError:
            return False

    def state(self) -> tuple[bool, bool]:
        """``(available, enabled)`` right now, never raising: a missing knob
        reads as unavailable and off."""
        try:
            return self.available(), self.enabled()
        except TurboUnavailable:
            return False, False

    def enabled(self) -> bool:
        """Live state: True when turbo is allowed (no_turbo == 0)."""
        try:
            with open(self.path) as f:
                return f.read().strip() == "0"
        except OSError as exc:
            raise TurboUnavailable(str(exc)) from exc

    def set_enabled(self, on: bool) -> None:
        """Allow (``on``) or forbid turbo. Failures come back as one plain
        sentence, because the GUI shows the message as it is."""
        try:
            with open(self.path, "w") as f:
                f.write("0" if on else "1")
        except FileNotFoundError as exc:
            raise TurboUnavailable(
                "this machine has no turbo boost switch to change") from exc
        except PermissionError as exc:
            # intel_pstate answers EPERM when turbo is disabled below it.
            raise TurboUnavailable(
                "turbo boost is switched off in the BIOS, or this CPU does "
                "not support it") from exc
        except OSError as exc:
            raise TurboUnavailable(
                f"turbo boost could not be changed: {exc.strerror or exc}") from exc
