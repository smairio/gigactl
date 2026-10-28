"""The checks every EC-writing tool passes before it touches the EC.

Two rules, the same ones gfan and gkbd follow (gigactl-cli-lib.sh detect_mode
and model_check). The daemon is the single writer of the EC, so a tool runs
only when the bus says, definitely, that the daemon is not running — and
"could not tell" refuses. And it writes only on a Gigabyte G5/G6/G7, unless
GIGACTL_TOOLS_UNSAFE=1 says otherwise.

No real busctl and no real DMI: the command runner and the DMI paths are
handed in.
"""
from __future__ import annotations

import subprocess

import pytest

import guard


class FakeBusctl:
    """Stands in for subprocess.run running busctl: prints ``output`` and exits
    with ``returncode``, or raises ``raises`` as if busctl could not be run."""

    def __init__(self, output="b false\n", returncode=0, raises=None):
        self.output = output
        self.returncode = returncode
        self.raises = raises
        self.calls = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, stdout=self.output)


# --- is the daemon running? ---------------------------------------------------

def test_a_stopped_daemon_lets_the_tool_run():
    assert guard.daemon_refusal(FakeBusctl("b false\n")) is None


def test_a_running_daemon_refuses_and_says_how_to_stop_it():
    """The daemon must stay the only writer. The refusal names the commands,
    because the person reading it is about to need them."""
    reason = guard.daemon_refusal(FakeBusctl("b true\n"))
    assert reason is not None
    assert "sudo systemctl stop gigactl" in reason
    assert "sudo systemctl start gigactl" in reason


@pytest.mark.parametrize("busctl", [
    FakeBusctl("wat\n"),
    FakeBusctl("Failed to connect to system bus: No such file or directory\n",
               returncode=1),
    FakeBusctl("b false\n", returncode=1),
], ids=["an answer that is neither yes nor no",
        "the bus call failed",
        "a failed call, whatever it printed"])
def test_anything_short_of_a_definite_no_refuses(busctl):
    """As in detect_mode: "could not tell" must never quietly become "wrote
    the EC behind the daemon's back"."""
    reason = guard.daemon_refusal(busctl)
    assert reason is not None
    assert "cannot tell whether the gigactl daemon is running" in reason
    assert busctl.output.strip() in reason, "busctl's own words help the reader"


def test_no_busctl_refuses_rather_than_guessing():
    missing = FileNotFoundError(2, "No such file or directory", "busctl")
    reason = guard.daemon_refusal(FakeBusctl(raises=missing))
    assert reason is not None
    assert "busctl not found" in reason


def test_it_asks_the_system_bus_about_the_daemons_own_name():
    """A question about any other name would always hear "b false", and let
    every tool write beside a running daemon. So this bus answers "b true" only
    to the question detect_mode asks."""
    def bus_with_the_daemon_on_it(argv, **_kwargs):
        asked = argv == ["busctl", "--system", "call", "org.freedesktop.DBus",
                         "/org/freedesktop/DBus", "org.freedesktop.DBus",
                         "NameHasOwner", "s", "io.github.smairio.gigactl"]
        return subprocess.CompletedProcess(
            argv, 0, stdout="b true\n" if asked else "b false\n")

    reason = guard.daemon_refusal(bus_with_the_daemon_on_it)
    assert reason is not None and "systemctl stop gigactl" in reason


# --- is this a Gigabyte G5/G6/G7? ----------------------------------------------

@pytest.mark.parametrize("vendor, model", [
    ("GIGABYTE", "G5 GE"),
    ("GIGABYTE", "G6 KF"),
    ("GIGABYTE", "G7 GD"),
    ("Gigabyte Technology Co., Ltd.", "G5 MF5"),
])
def test_a_gigabyte_g5_g6_or_g7_passes(vendor, model):
    assert guard.model_refusal(vendor, model) is None


def test_another_vendor_is_refused_and_the_override_is_named():
    reason = guard.model_refusal("Dell Inc.", "G6 KF")
    assert reason is not None
    assert "'Dell Inc.'" in reason
    assert "GIGACTL_TOOLS_UNSAFE=1" in reason


@pytest.mark.parametrize("model", ["AERO 15", ""])
def test_a_gigabyte_outside_the_family_is_refused(model):
    reason = guard.model_refusal("GIGABYTE", model)
    assert reason is not None
    assert f"'{model}'" in reason
    assert "GIGACTL_TOOLS_UNSAFE=1" in reason


# --- both checks, the way the tools ask ------------------------------------------

def dmi(tmp_path, vendor, model):
    """DMI files as the kernel publishes them, trailing newline included."""
    (tmp_path / "sys_vendor").write_text(vendor + "\n")
    (tmp_path / "product_name").write_text(model + "\n")
    return {"vendor_path": str(tmp_path / "sys_vendor"),
            "model_path": str(tmp_path / "product_name")}


def test_a_gigabyte_g5_with_the_daemon_stopped_may_be_written(tmp_path):
    assert guard.refusal(run=FakeBusctl("b false\n"), environ={},
                         **dmi(tmp_path, "GIGABYTE", "G5 GE")) is None


def test_another_machine_is_refused_even_with_the_daemon_stopped(tmp_path):
    reason = guard.refusal(run=FakeBusctl("b false\n"), environ={},
                           **dmi(tmp_path, "Dell Inc.", "XPS 15"))
    assert reason is not None and "'Dell Inc.'" in reason


def test_a_running_daemon_is_refused_on_a_supported_machine(tmp_path):
    reason = guard.refusal(run=FakeBusctl("b true\n"), environ={},
                           **dmi(tmp_path, "GIGABYTE", "G6 KF"))
    assert reason is not None and "systemctl stop gigactl" in reason


def test_unreadable_dmi_is_refused(tmp_path):
    """Like gfan's `cat ... || true`: no vendor string is not a Gigabyte."""
    reason = guard.refusal(run=FakeBusctl("b false\n"), environ={},
                           vendor_path=str(tmp_path / "absent"),
                           model_path=str(tmp_path / "absent too"))
    assert reason is not None and "vendor ''" in reason


def test_the_override_lets_another_machine_through(tmp_path):
    assert guard.refusal(run=FakeBusctl("b false\n"),
                         environ={"GIGACTL_TOOLS_UNSAFE": "1"},
                         **dmi(tmp_path, "Dell Inc.", "XPS 15")) is None


@pytest.mark.parametrize("value", ["0", "yes"])
def test_only_exactly_1_overrides(tmp_path, value):
    """As in gfan: GIGACTL_TOOLS_UNSAFE=0 reads as "not unsafe", and must not
    open the door."""
    reason = guard.refusal(run=FakeBusctl("b false\n"),
                           environ={"GIGACTL_TOOLS_UNSAFE": value},
                           **dmi(tmp_path, "Dell Inc.", "XPS 15"))
    assert reason is not None


def test_the_override_never_lets_a_tool_write_beside_the_daemon(tmp_path):
    """GIGACTL_TOOLS_UNSAFE is about the hardware only, the way GFAN_UNSAFE
    skips gfan's model check and never its daemon check."""
    reason = guard.refusal(run=FakeBusctl("b true\n"),
                           environ={"GIGACTL_TOOLS_UNSAFE": "1"},
                           **dmi(tmp_path, "Dell Inc.", "XPS 15"))
    assert reason is not None and "systemctl stop gigactl" in reason
