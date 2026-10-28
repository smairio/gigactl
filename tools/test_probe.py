"""Safety contract for tools/probe.sh: it must stay read-only.

The probe is the first tool run on a model nobody here has tested. If an edit
adds a write (EC doorbell, sysfs store, modprobe, dd of=...), these tests
fail before the script ever touches a machine.
"""
import pathlib
import re

PROBE = pathlib.Path(__file__).with_name("probe.sh").read_text()

FORBIDDEN = [
    r"\bdd\b[^#\n]*\bof=",          # dd writing to a device/file
    r">\s*/sys/",                   # shell redirection into sysfs
    r">\s*/dev/port",               # raw port I/O target
    r"\bmodprobe\b",                # loading modules changes system state
    r"\bec_probe\b[^#\n]*\bwrite\b",
    r"0xC1|0xCA|0xC4",              # EC doorbell commands are writes
]


def test_no_write_operations():
    for pattern in FORBIDDEN:
        assert not re.search(pattern, PROBE), f"probe.sh must stay read-only, found: {pattern}"


def test_reads_documented_ec_offsets_only():
    for off in ("07", "0A", "0B", "CE", "CF", "D0", "D3"):
        assert off in PROBE
    assert "docs/PROTOCOL.md" in PROBE


def test_fan_check_always_restores_and_refuses_hot():
    tool = pathlib.Path(__file__).with_name("fan_check.py").read_text()
    assert "finally:" in tool and "restore_auto" in tool, \
        "the controlled write must hand the fan back whatever happens"
    assert "MAX_START_TEMP_C" in tool, "must refuse to run on a hot machine"
    assert "TEST_DUTY_PCT = 80" in tool, "distinctive, cooling-increasing duty"


def test_daemon_smoke_always_restores_firmware():
    tool = pathlib.Path(__file__).with_name("daemon_smoke.py").read_text()
    assert "finally:" in tool and "restore_auto" in tool
    assert 'set_profile("firmware")' in tool


def test_ec_read_reads_under_the_daemons_lock_and_never_writes(fake_ec, capsys):
    """The probe's EC reader goes through the daemon's own telemetry read, so
    every register is read inside a transaction — holding the lock the daemon
    and gfan take — and nothing is written."""
    import ec_read

    fake_ec.regs.update({0x07: 43, 0x0A: 40, 0xCE: 71, 0xD0: 0x03, 0xD1: 0x8D})
    assert ec_read.main(fake_ec) == 0
    assert fake_ec.reads, "it should have read the telemetry registers"
    assert fake_ec.unlocked == [], "every read must happen under the EC lock"
    assert fake_ec.writes == []
    assert capsys.readouterr().out.splitlines() == [
        f"sample {n}: cpu=43C gpu=40C dut1=27% dut2=0% rpm1~2372 rpm2~0"
        for n in range(3)]
