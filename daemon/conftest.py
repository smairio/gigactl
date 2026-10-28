# Presence of this file makes pytest add daemon/ to sys.path, so tests can
# `import gigactld` without an editable install.
#
# It also holds the fakes the Daemon-level tests share (test_daemon_keyboard,
# test_daemon_properties): one recording EC, a permissive authorizer, and
# stand-ins for the D-Bus connection and method invocation. The protocol-level
# tests (test_fans, test_keyboard, test_mailbox) keep their own narrower fakes.
from contextlib import nullcontext

import pytest


class FakeEc:
    """Records writes; unwritten registers read 0, which is enough for the
    apply-now path (tach period 0 -> 0 rpm).

    It emulates exactly one real EC behaviour: a fan-duty command echoes into
    that fan's duty register, because the daemon reads it back to verify the
    write and would otherwise treat every duty as refused.
    """

    backend = type("B", (), {"name": "fake"})()

    def __init__(self):
        self.writes = []  # (offset, value) in order
        self.regs = {}
        # Set to a sentence to emulate an EC that reads but will not write —
        # the read-only ec_sys of #25. None means writes land.
        self.unwritable = None
        # Set to an exception to emulate a write that fails for a reason we
        # could not have known in advance — as opposed to `unwritable`, which
        # is the gate we can read before trying.
        self.write_error = None

    def unwritable_reason(self):
        return self.unwritable

    def write_u8(self, off, val):
        from gigactld import ec, fans, mailbox

        if self.unwritable:
            raise ec.EcNotWritable(self.unwritable)
        if self.write_error:
            raise self.write_error
        self.writes.append((off, val))
        self.regs[off] = val
        if off == mailbox.FCMD and val == fans.DOORBELL:
            fan = self.regs.get(mailbox.FDAT)
            duty = self.regs.get(mailbox.FBUF, 0)
            if fan in fans.DUTY_REG:
                self.regs[fans.DUTY_REG[fan]] = duty

    def read_u8(self, off):
        return self.regs.get(off, 0)

    def read_u16_be(self, hi, lo):
        return (self.read_u8(hi) << 8) | self.read_u8(lo)

    def transaction(self):
        return nullcontext()


class AllowAllAuthorizer:
    def check(self, sender, action):
        return True


class RecordingConn:
    """Stands in for the D-Bus connection, capturing emitted signals."""

    def __init__(self):
        self.emissions = []  # (iface, signal, unpacked params)

    def emit_signal(self, dest, path, iface, signal, params):
        self.emissions.append((iface, signal, params.unpack()))

    def properties_changed(self):
        """The (interface, changed, invalidated) of the last PropertiesChanged."""
        emitted = [e for e in self.emissions if e[1] == "PropertiesChanged"]
        return emitted[-1][2] if emitted else None


class Invocation:
    """Stands in for a Gio method invocation."""

    def __init__(self):
        self.returned = False
        self.error = None

    def return_value(self, _v):
        self.returned = True

    def return_dbus_error(self, name, msg):
        self.error = (name, msg)


@pytest.fixture
def make_daemon(tmp_path):
    """Factory for a Daemon wired to fakes. ``connected=False`` leaves ``_conn``
    unset, which is the pre-bus state. State and the turbo knob live under
    ``tmp_path`` so a test never touches the real /var/lib/gigactl/state.json or
    /sys/devices/system/cpu/intel_pstate/no_turbo."""
    from gigactld import turbo
    from gigactld.service import Daemon

    count = 0

    def factory(connected: bool = True):
        nonlocal count
        count += 1
        daemon = Daemon(FakeEc(), authorizer=AllowAllAuthorizer())
        daemon.state_path = str(tmp_path / f"state{count}.json")
        # Never the real intel_pstate knob: a test run as root would otherwise
        # change the CPU's turbo setting. Absent by default (no turbo switch);
        # a test that wants one writes this file or swaps in its own.
        daemon.turbo = turbo.TurboControl(str(tmp_path / f"no_turbo{count}"))
        if connected:
            daemon._conn = RecordingConn()
        return daemon

    return factory
