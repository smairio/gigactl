"""gkbd's two modes, and the store that stops being written when the daemon is up.

The keyboard case has a second hazard beyond the shared mailbox: state. The
daemon persists colour and brightness to /var/lib/gigactl/state.json and
re-applies it at boot and resume, while gkbd's own store is /var/lib/gkbd. Two
stores for one backlight is how a stale value wins a race — so in daemon mode
gkbd must not write its store at all, and must report the daemon's state rather
than its own.
"""
from __future__ import annotations


def _state_file(sandbox):
    return sandbox.state_dir / "gkbd" / "state"


# --- with the daemon up -----------------------------------------------------

def test_a_named_colour_goes_through_the_daemon(sandbox):
    result = sandbox.run("gkbd", "red", daemon=True)
    assert result.returncode == 0, result.output
    assert sandbox.dbus_calls() == [["SetKeyboardColor", "uuu", "255", "0", "0"]]
    assert sandbox.ec_calls == []


def test_a_hex_colour_goes_through_the_daemon(sandbox):
    sandbox.run("gkbd", "#ff6600", daemon=True)
    assert sandbox.dbus_calls() == [["SetKeyboardColor", "uuu", "255", "102", "0"]]


def test_brightness_goes_through_the_daemon_as_a_percentage(sandbox):
    """The daemon's API speaks percent; only the EC wants 0-255."""
    result = sandbox.run("gkbd", "brightness", "50", daemon=True)
    assert result.returncode == 0, result.output
    assert sandbox.dbus_calls() == [["SetKeyboardBrightness", "u", "50"]]
    assert sandbox.ec_calls == []


def test_off_and_on_go_through_the_daemon(sandbox):
    sandbox.run("gkbd", "off", daemon=True)
    assert sandbox.dbus_calls() == [["SetKeyboardEnabled", "b", "false"]]


def test_on_goes_through_the_daemon(sandbox):
    sandbox.run("gkbd", "on", daemon=True)
    assert sandbox.dbus_calls() == [["SetKeyboardEnabled", "b", "true"]]


def test_daemon_mode_never_writes_the_second_store(sandbox):
    """/var/lib/gkbd racing /var/lib/gigactl/state.json is the conflict #22
    documented; with the daemon up there is one store and it is not ours."""
    sandbox.run("gkbd", "red", daemon=True)
    sandbox.run("gkbd", "brightness", "40", daemon=True)
    assert not _state_file(sandbox).exists()


def test_status_reports_what_the_daemon_holds(sandbox):
    result = sandbox.run("gkbd", "status", daemon=True)
    assert result.returncode == 0, result.output
    # the fake daemon holds R=255 G=128 B=0 at 60%
    assert "R=255" in result.stdout and "G=128" in result.stdout and "B=0" in result.stdout
    assert "60%" in result.stdout
    assert sandbox.properties_read() == ["KeyboardState"]
    assert sandbox.ec_calls == []


def test_apply_is_a_no_op_the_daemon_already_does(sandbox):
    """The legacy gkbd-restore.service is what used to call this; the daemon
    re-applies at boot and resume itself now."""
    result = sandbox.run("gkbd", "apply", daemon=True)
    assert result.returncode == 0, result.output
    assert sandbox.ec_calls == []
    assert sandbox.dbus_calls() == []
    assert "daemon" in result.output.lower()


def test_daemon_mode_needs_neither_root_nor_ec_probe(sandbox):
    result = sandbox.run("gkbd", "red", daemon=True, unprivileged=True,
                         extra_env={"GKBD_EC_PROBE": "/nonexistent/ec_probe"})
    assert result.returncode == 0, result.output
    assert sandbox.sudo_calls == []


def test_a_refusal_from_the_daemon_is_reported(sandbox):
    result = sandbox.run("gkbd", "red", daemon=True,
                         fail_method="SetKeyboardColor",
                         fail_message="not authorized")
    assert result.returncode != 0
    assert "not authorized" in result.output
    assert sandbox.ec_calls == []


def test_an_unknown_colour_is_refused_before_any_call(sandbox):
    result = sandbox.run("gkbd", "chartreuse", daemon=True)
    assert result.returncode != 0
    assert "chartreuse" in result.output
    assert sandbox.dbus_calls() == []


# --- with no daemon ---------------------------------------------------------

def test_without_a_daemon_it_writes_the_mailbox_in_the_ec_byte_order(sandbox):
    """The EC wants B, R, G — the one detail in PROTOCOL.md most likely to be
    'corrected' into RGB by someone reading the code."""
    result = sandbox.run("gkbd", "red", daemon=False)
    assert result.returncode == 0, result.output
    assert sandbox.dbus_calls() == []
    writes = sandbox.ec_writes
    assert (0xF9, 0x03) in writes          # colour command
    assert (0xFA, 0) in writes             # blue  -> FBUF
    assert (0xFB, 255) in writes           # red   -> FBF1
    assert (0xFC, 0) in writes             # green -> FBF2
    assert (0xF8, 0xCA) in writes          # LED doorbell
    assert (0xF8, 0xC4) in writes          # master enable came first
    assert writes.index((0xF8, 0xC4)) < writes.index((0xF9, 0x03))


def test_without_a_daemon_it_keeps_its_own_store(sandbox):
    sandbox.run("gkbd", "green", daemon=False)
    assert _state_file(sandbox).read_text().split()[:3] == ["0", "255", "0"]


def test_without_a_daemon_brightness_scales_to_the_register(sandbox):
    sandbox.run("gkbd", "brightness", "50", daemon=False)
    assert (0xFA, 127) in sandbox.ec_writes


def test_without_a_daemon_status_reads_its_own_store(sandbox):
    sandbox.run("gkbd", "blue", daemon=False)
    result = sandbox.run("gkbd", "status", daemon=False)
    assert "B=200" in result.stdout
    assert sandbox.properties_read() == []


def test_a_missing_ec_probe_is_only_fatal_without_a_daemon(sandbox):
    result = sandbox.run("gkbd", "red", daemon=False,
                         extra_env={"GKBD_EC_PROBE": "/nonexistent/ec_probe"})
    assert result.returncode != 0
    assert "ec_probe" in result.output


def test_an_unprivileged_direct_invocation_re_execs_under_sudo(sandbox):
    sandbox.run("gkbd", "red", daemon=False, unprivileged=True)
    assert sandbox.sudo_calls, "expected a sudo re-exec"
    assert sandbox.ec_writes == []


# --- serialisation (AC3) ----------------------------------------------------

def test_a_direct_write_waits_for_whoever_holds_the_ec_lock(sandbox):
    holder = sandbox.hold_lock()
    try:
        result = sandbox.run("gkbd", "red", daemon=False,
                             extra_env={"GIGACTL_LOCK_WAIT": "1"})
    finally:
        holder.kill()
        holder.wait()
    assert result.returncode != 0
    assert "lock" in result.output.lower() or "busy" in result.output.lower()
    assert sandbox.ec_writes == []
