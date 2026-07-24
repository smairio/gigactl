"""A sandbox for driving gfan/gkbd without hardware, root, or a bus.

The two scripts have two modes now: unprivileged D-Bus client when the daemon
owns its bus name, direct EC writer when it does not. Both are shell, and the
interesting behaviour is *which external commands get run* — so the sandbox puts
recording stubs for ``busctl``, ``ec_probe``, ``id`` and ``sudo`` at the front of
PATH and lets the tests read back exactly what the script tried to do. ``flock``
is deliberately the real one: the locking is the point of one acceptance
criterion, not something to fake.

The scripts take their ec_probe path, state file and lock path from the
environment (defaulting to the real locations) purely so this suite can run as an
ordinary user in a tmp dir.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUS = "io.github.smairio.gigactl"


def source_text(rel: str) -> str:
    """A repo file as text, for assertions about the scripts' own source."""
    return (ROOT / rel).read_text()

# What the fake daemon answers property reads with. Six numbers in the Telemetry
# signal's order: cpu, gpu, fan1 rpm, fan2 rpm, fan1 duty %, fan2 duty %.
DEFAULT_PROPS = {
    "Telemetry": "(uuuuuu) 61 50 4200 4000 60 56",
    "ActiveProfile": 's "manual"',
    "KeyboardState": "(buuuu) true 255 128 0 60",
    "DaemonVersion": 's "1.0.0"',
}

_BUSCTL = r'''#!/usr/bin/python3
"""Recording stand-in for busctl: answers NameHasOwner from STUB_DAEMON ("1",
"0", "error" — the bus call fails, or "garbage" — it answers nonsense), serves
canned property reads, and logs every call. STUB_FAIL_METHOD makes a method
fail; STUB_FAIL_WHEN narrows that to calls whose arguments contain it, so a test
can fail the second of two calls."""
import json, os, sys

args = sys.argv[1:]
log = os.environ["STUB_LOG_DIR"] + "/busctl.log"
with open(log, "a") as f:
    f.write(" ".join(args) + "\n")

props = json.loads(os.environ.get("STUB_PROPS", "{}"))
fail = os.environ.get("STUB_FAIL_METHOD", "")
fail_when = os.environ.get("STUB_FAIL_WHEN", "")
daemon = os.environ.get("STUB_DAEMON", "0")

if "NameHasOwner" in args:
    if daemon == "error":
        print("Failed to connect to system bus: No such file or directory",
              file=sys.stderr)
        sys.exit(1)
    if daemon == "garbage":
        print("wat")
        sys.exit(0)
    print("b true" if daemon == "1" else "b false")
    sys.exit(0)
if "get-property" in args:
    name = args[-1]
    if name not in props:
        print(f"Failed to get property {name}: unknown", file=sys.stderr)
        sys.exit(1)
    print(props[name])
    sys.exit(0)
if "call" in args:
    method = args[args.index("call") + 4]
    if method == fail and (not fail_when or fail_when in " ".join(args)):
        print(f"Call failed: {os.environ.get('STUB_FAIL_MESSAGE', 'refused')}",
              file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
print(f"stub busctl: unhandled {args}", file=sys.stderr)
sys.exit(2)
'''

_EC_PROBE = r'''#!/usr/bin/python3
"""Recording stand-in for ec_probe. Reads come from STUB_REGS (offset -> value),
defaulting to 0; a fan-duty doorbell echoes into that fan's duty register the way
the real EC does, so gfan's read-back verification converges."""
import json, os, sys

args = sys.argv[1:]
log = os.environ["STUB_LOG_DIR"] + "/ec_probe.log"
regs_path = os.environ["STUB_LOG_DIR"] + "/regs.json"

if os.path.exists(regs_path):
    regs = json.load(open(regs_path))
else:
    regs = {str(int(k, 0)): v for k, v in
            json.loads(os.environ.get("STUB_REGS", "{}")).items()}

def save():
    json.dump(regs, open(regs_path, "w"))

with open(log, "a") as f:
    f.write(" ".join(args) + "\n")

if args[0] == "read":
    print(regs.get(str(int(args[1], 0)), 0))
elif args[0] == "write":
    off, val = int(args[1], 0), int(args[2], 0)
    regs[str(off)] = val
    if off == 0xF8 and val == 0xC1:            # fan doorbell
        fan, duty = regs.get(str(0xF9)), regs.get(str(0xFA), 0)
        if fan == 1:
            regs[str(0xCE)] = duty
        elif fan == 2:
            regs[str(0xCF)] = duty
    save()
else:
    print(f"stub ec_probe: unhandled {args}", file=sys.stderr)
    sys.exit(2)
'''

_ID = '#!/bin/sh\n# pretend to be root so the direct path does not re-exec us\necho 0\n'
_SUDO = ('#!/bin/sh\n# record the re-exec instead of performing it (no recursion)\n'
         'echo "$@" >> "$STUB_LOG_DIR/sudo.log"\n')


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


class Sandbox:
    def __init__(self, tmp_path: Path):
        self.dir = tmp_path
        self.logs = tmp_path / "logs"
        self.logs.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.state_dir = tmp_path / "state"
        self.state_dir.mkdir()
        self.lock = tmp_path / "gigactl-ec.lock"
        self.props = dict(DEFAULT_PROPS)
        for name, body in (("busctl", _BUSCTL), ("ec_probe", _EC_PROBE),
                           ("id", _ID), ("sudo", _SUDO)):
            path = self.bin / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

    def _bin_without_busctl(self) -> Path:
        """A PATH directory with the stubs and just enough coreutils, but no
        busctl anywhere. PATH cannot simply drop /usr/bin — the scripts need cat,
        readlink and friends before they ever look for busctl."""
        d = self.dir / "bin-no-busctl"
        if not d.exists():
            d.mkdir()
            for stub in ("ec_probe", "id", "sudo"):
                (d / stub).symlink_to(self.bin / stub)
            for tool in ("cat", "readlink", "dirname", "awk", "flock", "sleep",
                         "mkdir", "rm", "date", "clear"):
                real = shutil.which(tool)
                if real:
                    (d / tool).symlink_to(real)
        return d

    # --- running -------------------------------------------------------------
    def run(self, tool: str, *args: str, daemon: bool | str,
            regs: dict | None = None,
            fail_method: str = "", fail_when: str = "", fail_message: str = "refused",
            unprivileged: bool = False, no_busctl: bool = False,
            extra_env: dict | None = None) -> Result:
        env = dict(os.environ)
        env.update({
            "PATH": f"{self.bin}:{env['PATH']}",
            "STUB_LOG_DIR": str(self.logs),
            # bools keep their obvious meaning; a string ("error", "garbage")
            # makes the detection call itself misbehave.
            "STUB_DAEMON": daemon if isinstance(daemon, str)
                           else ("1" if daemon else "0"),
            "STUB_PROPS": json.dumps(self.props),
            "STUB_FAIL_METHOD": fail_method,
            "STUB_FAIL_WHEN": fail_when,
            "STUB_FAIL_MESSAGE": fail_message,
            "STUB_REGS": json.dumps(regs or {}),
            "GIGACTL_EC_LOCK": str(self.lock),
            "GFAN_EC_PROBE": str(self.bin / "ec_probe"),
            "GKBD_EC_PROBE": str(self.bin / "ec_probe"),
            "GFAN_STATE": str(self.state_dir / "gfan.mode"),
            "GKBD_STATE_DIR": str(self.state_dir / "gkbd"),
            # model_check is pre-existing behaviour and reads real DMI; skip it so
            # the suite passes on any machine.
            "GFAN_UNSAFE": "1",
            "GKBD_UNSAFE": "1",
        })
        if unprivileged:
            # let the script see a non-root uid, so the sudo re-exec is exercised
            (self.bin / "id").write_text("#!/bin/sh\necho 1000\n")
        if no_busctl:
            env["PATH"] = str(self._bin_without_busctl())
        env.update(extra_env or {})
        proc = subprocess.run([str(ROOT / tool), *args], env=env,
                              capture_output=True, text=True, timeout=90)
        return Result(proc.returncode, proc.stdout, proc.stderr)

    # --- reading back --------------------------------------------------------
    def _log(self, name: str) -> list[str]:
        path = self.logs / f"{name}.log"
        return path.read_text().splitlines() if path.exists() else []

    @property
    def ec_writes(self) -> list[tuple[int, int]]:
        return [(int(p[1], 0), int(p[2], 0))
                for p in (shlex.split(line) for line in self._log("ec_probe"))
                if p[0] == "write"]

    @property
    def ec_calls(self) -> list[str]:
        return self._log("ec_probe")

    @property
    def sudo_calls(self) -> list[str]:
        return self._log("sudo")

    def dbus_calls(self) -> list[list[str]]:
        """Method calls to the daemon, as [method, *args] — detection excluded."""
        calls = []
        for line in self._log("busctl"):
            parts = shlex.split(line)
            if "call" in parts and BUS in parts and "NameHasOwner" not in parts:
                index = parts.index("call")
                calls.append(parts[index + 4:])
        return calls

    def properties_read(self) -> list[str]:
        return [shlex.split(line)[-1] for line in self._log("busctl")
                if "get-property" in line]

    def hold_lock(self):
        """Hold the EC lock the way a concurrent invocation would."""
        return subprocess.Popen(["flock", "-x", str(self.lock), "sleep", "60"])


@pytest.fixture
def sandbox(tmp_path):
    return Sandbox(tmp_path)
