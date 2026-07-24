"""gfan's two modes: D-Bus client when the daemon is up, direct EC writer when not.

The rule the whole ticket rests on is that gfan never writes the EC while the
daemon owns it — the daemon is the single writer, and two processes taking turns
on a mailbox that needs three ordered writes per command is exactly how a fan
ends up stuck at someone else's duty. So every daemon-mode test asserts the
ec_probe log is *empty*, not merely that the D-Bus call happened.
"""
from __future__ import annotations

from conftest import source_text

DOORBELL = (0xF8, 0xC1)


# --- with the daemon up -----------------------------------------------------

def test_setting_a_duty_goes_through_the_daemon_and_touches_no_registers(sandbox):
    """One call with selector 0 (= both fans), so the daemon verifies and — on a
    refusal — reverts the pair together; two calls could strand fan1 alone."""
    result = sandbox.run("gfan", "70", daemon=True)
    assert result.returncode == 0, result.output
    assert sandbox.dbus_calls() == [["SetFanDuty", "uu", "0", "70"]]
    assert sandbox.ec_calls == []


def test_two_percentages_map_to_the_two_fans(sandbox):
    sandbox.run("gfan", "40", "90", daemon=True)
    assert sandbox.dbus_calls() == [["SetFanDuty", "uu", "1", "40"],
                                    ["SetFanDuty", "uu", "2", "90"]]


def test_a_refused_second_duty_reverts_rather_than_stranding_fan1(sandbox):
    """Split duties need two calls; if the second is refused, fan1 is already
    manual — hand both back to firmware, the way direct mode's revert_auto does."""
    result = sandbox.run("gfan", "40", "90", daemon=True,
                         fail_method="SetFanDuty", fail_when="uu 2",
                         fail_message="EC refused the duty write")
    assert result.returncode != 0
    assert "revert" in result.output.lower()
    assert sandbox.dbus_calls() == [["SetFanDuty", "uu", "1", "40"],
                                    ["SetFanDuty", "uu", "2", "90"],
                                    ["RestoreFirmware"]]
    assert sandbox.ec_calls == []


def test_max_asks_for_full_duty_on_both_fans(sandbox):
    sandbox.run("gfan", "max", daemon=True)
    assert sandbox.dbus_calls() == [["SetFanDuty", "uu", "0", "100"]]
    assert sandbox.ec_calls == []


def test_auto_hands_the_fans_back_through_the_daemon(sandbox):
    result = sandbox.run("gfan", "auto", daemon=True)
    assert result.returncode == 0, result.output
    assert sandbox.dbus_calls() == [["RestoreFirmware"]]
    assert sandbox.ec_calls == []


def test_status_reads_the_daemon_rather_than_the_hardware(sandbox):
    result = sandbox.run("gfan", "status", daemon=True)
    assert result.returncode == 0, result.output
    assert "CPU: 61°C" in result.stdout and "GPU: 50°C" in result.stdout
    assert "duty  60%" in result.stdout and "~4200 RPM" in result.stdout
    assert "duty  56%" in result.stdout and "~4000 RPM" in result.stdout
    assert "manual" in result.stdout          # the daemon's own ActiveProfile
    assert sandbox.ec_calls == []
    assert set(sandbox.properties_read()) == {"Telemetry", "ActiveProfile"}


def test_daemon_mode_needs_no_root(sandbox):
    """polkit already lets the console user do this without a password, so
    re-execing under sudo would only add a prompt."""
    result = sandbox.run("gfan", "70", daemon=True, unprivileged=True)
    assert result.returncode == 0, result.output
    assert sandbox.sudo_calls == []
    assert sandbox.dbus_calls()


def test_daemon_mode_works_with_no_ec_probe_installed(sandbox):
    result = sandbox.run("gfan", "70", daemon=True,
                         extra_env={"GFAN_EC_PROBE": "/nonexistent/ec_probe"})
    assert result.returncode == 0, result.output
    assert sandbox.dbus_calls()


def test_a_refusal_from_the_daemon_is_reported_and_nothing_is_written(sandbox):
    result = sandbox.run("gfan", "70", daemon=True, fail_method="SetFanDuty",
                         fail_message="EC refused the duty write")
    assert result.returncode != 0
    assert "EC refused the duty write" in result.output
    assert "sudo" not in result.output  # the remedy hint is for auth failures only
    assert sandbox.ec_calls == []


def test_an_authorization_refusal_points_at_its_remedy(sandbox):
    """Over SSH there is no polkit agent, so allow_inactive=auth_admin refuses —
    but root is authorized outright, so 'run it with sudo' is the way out. The
    old script sudo'd implicitly; the message has to carry that knowledge now."""
    result = sandbox.run("gfan", "70", daemon=True, fail_method="SetFanDuty",
                         fail_message="not authorized to control the fans")
    assert result.returncode != 0
    assert "not authorized" in result.output
    assert "sudo" in result.output


def test_the_hot_and_slow_guard_uses_the_daemon_temperature(sandbox):
    sandbox.props["Telemetry"] = "(uuuuuu) 91 60 4200 4000 60 56"
    result = sandbox.run("gfan", "20", daemon=True)
    assert result.returncode != 0
    assert "91" in result.output and "refusing" in result.output
    assert sandbox.dbus_calls() == []


def test_force_overrides_the_hot_and_slow_guard_in_daemon_mode(sandbox):
    sandbox.props["Telemetry"] = "(uuuuuu) 91 60 4200 4000 60 56"
    result = sandbox.run("gfan", "20", "--force", daemon=True)
    assert result.returncode == 0, result.output
    assert sandbox.dbus_calls() == [["SetFanDuty", "uu", "0", "20"]]


# --- when it cannot tell ------------------------------------------------------

def test_an_unanswerable_daemon_check_refuses_rather_than_writing(sandbox):
    """The rule is 'never write the EC while the daemon is active'. A detection
    that fails open would break it in exactly the situations where it matters —
    so 'could not tell' refuses instead of falling back to direct."""
    result = sandbox.run("gfan", "70", daemon="error")
    assert result.returncode != 0
    assert "cannot tell" in result.output
    assert sandbox.ec_calls == []
    assert sandbox.sudo_calls == []
    assert sandbox.dbus_calls() == []


def test_a_nonsense_answer_from_the_bus_also_refuses(sandbox):
    result = sandbox.run("gfan", "70", daemon="garbage")
    assert result.returncode != 0
    assert "cannot tell" in result.output
    assert sandbox.ec_calls == []


def test_a_machine_without_busctl_refuses_rather_than_writing(sandbox):
    result = sandbox.run("gfan", "70", daemon=False, no_busctl=True)
    assert result.returncode != 0
    assert "busctl not found" in result.output
    assert sandbox.ec_calls == []
    assert sandbox.sudo_calls == []


# --- with no daemon ---------------------------------------------------------

def test_without_a_daemon_it_still_writes_the_ec_itself(sandbox):
    result = sandbox.run("gfan", "70", daemon=False)
    assert result.returncode == 0, result.output
    assert sandbox.dbus_calls() == []
    writes = sandbox.ec_writes
    # one mailbox sequence per fan: selector, duty, doorbell last
    assert (0xF9, 1) in writes and (0xF9, 2) in writes
    assert writes.count(DOORBELL) == 2
    assert writes[-1] == DOORBELL
    duty = 70 * 255 // 100
    assert (0xFA, duty) in writes


def test_without_a_daemon_auto_writes_the_auto_sentinel(sandbox):
    sandbox.run("gfan", "auto", daemon=False)
    assert sandbox.dbus_calls() == []
    assert (0xF9, 0xFF) in sandbox.ec_writes


def test_without_a_daemon_status_reads_the_registers(sandbox):
    result = sandbox.run("gfan", "status", daemon=False,
                         regs={"0x07": 71, "0x0A": 64, "0xCE": 255, "0xCF": 128})
    assert result.returncode == 0, result.output
    assert "CPU: 71°C" in result.stdout and "GPU: 64°C" in result.stdout
    assert "duty 100%" in result.stdout and "duty  50%" in result.stdout
    assert sandbox.properties_read() == []


def test_a_missing_ec_probe_is_only_fatal_without_a_daemon(sandbox):
    result = sandbox.run("gfan", "70", daemon=False,
                         extra_env={"GFAN_EC_PROBE": "/nonexistent/ec_probe"})
    assert result.returncode != 0
    assert "ec_probe" in result.output


def test_an_unprivileged_direct_invocation_re_execs_under_sudo(sandbox):
    """Direct register access needs root; the daemon path does not."""
    sandbox.run("gfan", "70", daemon=False, unprivileged=True)
    assert sandbox.sudo_calls, "expected a sudo re-exec"
    assert "70" in sandbox.sudo_calls[0]
    assert sandbox.ec_writes == []


# --- serialisation (AC3) ----------------------------------------------------

def test_a_direct_write_waits_for_whoever_holds_the_ec_lock(sandbox):
    """Two concurrent direct-mode invocations must not interleave their mailbox
    sequences. gfan takes the same /run/lock/gigactl-ec.lock the daemon takes, so
    the second one waits — and says so rather than corrupting the first."""
    holder = sandbox.hold_lock()
    try:
        result = sandbox.run("gfan", "70", daemon=False,
                             extra_env={"GIGACTL_LOCK_WAIT": "1"})
    finally:
        holder.kill()
        holder.wait()
    assert result.returncode != 0
    assert "lock" in result.output.lower() or "busy" in result.output.lower()
    assert sandbox.ec_writes == [], "it must not write while another holds the lock"


def test_a_direct_write_proceeds_once_the_lock_is_free(sandbox):
    result = sandbox.run("gfan", "70", daemon=False,
                         extra_env={"GIGACTL_LOCK_WAIT": "1"})
    assert result.returncode == 0, result.output
    assert sandbox.ec_writes


def test_the_lock_is_the_one_the_daemon_takes():
    """A different path would serialise nothing. The lock lives once, in the
    shared lib, and both scripts source that lib rather than carrying copies."""
    assert 'LOCK_PATH = "/run/lock/gigactl-ec.lock"' in \
        source_text("daemon/gigactld/ec.py")
    assert "/run/lock/gigactl-ec.lock" in source_text("gigactl-cli-lib.sh")
    for tool in ("gfan", "gkbd"):
        body = source_text(tool)
        assert "gigactl-cli-lib.sh" in body, tool
        assert "/run/lock/" not in body, f"{tool} carries its own lock path"
