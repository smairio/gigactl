#!/usr/bin/env python3
"""Live smoke test: the real Daemon driving the real EC.

    sudo python3 tools/daemon_smoke.py

Runs the daemon's own tick loop in-process (no D-Bus) with the
'performance' profile for ~8s, printing telemetry each tick, then ALWAYS
hands both fans back to firmware auto in a finally block. This exercises
the exact code path gigactld runs — profile engine, mailbox writes,
verify-by-telemetry — on the EC backend gigactld itself would pick.
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "daemon"))

import guard  # noqa: E402
from gigactld import fans, telemetry  # noqa: E402
from gigactld.ec import EmbeddedController  # noqa: E402
from gigactld.service import Daemon  # noqa: E402


class _Conn:
    def emit_signal(self, *a):
        pass


def main() -> int:
    # The daemon's own backend choice: ec_sys if loaded, /dev/port only if
    # not. Creating it touches no hardware; the checks come first.
    ec = EmbeddedController()
    # The daemon check matters most here: this runs a second ProfileEngine,
    # and two engines driving the same fans would fight over every tick.
    reason = guard.refusal() or ec.unwritable_reason()
    if reason:
        print(f"daemon_smoke: {reason}", file=sys.stderr)
        return 1
    daemon = Daemon(ec)
    daemon._conn = _Conn()
    daemon.state_path = "/tmp/gigactl-smoke-state.json"
    daemon.engine.set_profile("performance")
    print(">>> daemon driving fans with 'performance' for ~8s <<<", flush=True)
    try:
        for i in range(4):
            daemon._tick()
            t = telemetry.read_telemetry(ec)
            print(f"tick {i}: cpu={t.cpu_temp}C gpu={t.gpu_temp}C "
                  f"dut1={t.fan1_duty_pct}% dut2={t.fan2_duty_pct}% "
                  f"rpm1={t.fan1_rpm} rpm2={t.fan2_rpm} "
                  f"fault={daemon._engine_fault}", flush=True)
            time.sleep(2)
    finally:
        # Not optional: firmware gets its fans back whatever happened.
        daemon.engine.set_profile("firmware")
        for f in fans.FANS:
            fans.restore_auto(ec, f)
        print(">>> fans handed back to firmware auto <<<", flush=True)
    # The duty register keeps the last commanded value until the firmware
    # loop recomputes; give it a few seconds before judging the handback.
    time.sleep(4)
    t = telemetry.read_telemetry(ec)
    print(f"final: dut1={t.fan1_duty_pct}% dut2={t.fan2_duty_pct}% "
          f"profile={daemon.engine.profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
