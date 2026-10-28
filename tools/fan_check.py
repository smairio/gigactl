#!/usr/bin/env python3
"""Drive ONE fan to a distinctive duty and watch both duty registers.

This is the first controlled EC *write* on an unverified model, designed to
settle the DUT1/DUT2 question in docs/PROTOCOL.md without guessing:

    sudo python3 tools/fan_check.py --fan 1

It refuses to run hot, drives the chosen fan to 80% for ~5s while sampling
both duty registers and RPMs, and ALWAYS hands the fan back to firmware
auto afterwards (finally block), whatever happens. While it runs, listen /
feel which physical side (left/right when facing the screen) spins up and
note it — register + physical side together decide the mapping.

80% is a cooling-increasing duty: the failure mode of this test is a
louder laptop for five seconds, never a hotter one.
"""
import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "daemon"))

import guard  # noqa: E402
from gigactld import fans, telemetry  # noqa: E402
from gigactld.ec import EmbeddedController  # noqa: E402

TEST_DUTY_PCT = 80
MAX_START_TEMP_C = 75
SAMPLES = 5


def sample(ec, label):
    t = telemetry.read_telemetry(ec)
    print(f"{label}: cpu={t.cpu_temp}C gpu={t.gpu_temp}C "
          f"dut1={t.fan1_duty_pct}% dut2={t.fan2_duty_pct}% "
          f"rpm1={t.fan1_rpm} rpm2={t.fan2_rpm}", flush=True)
    return t


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fan", type=int, choices=(1, 2), required=True)
    args = parser.parse_args()
    # The daemon's own backend choice: ec_sys if loaded, /dev/port only if
    # not. Creating it touches no hardware; the checks come first.
    ec = EmbeddedController()
    reason = guard.refusal() or ec.unwritable_reason()
    if reason:
        print(f"fan_check: {reason}", file=sys.stderr)
        return 1
    if pathlib.Path("/proc/self/status").exists() and _uid() != 0:
        print("fan_check: run with sudo (the EC needs root)")
        return 2
    start = sample(ec, "before")
    if start.cpu_temp >= MAX_START_TEMP_C or start.gpu_temp >= MAX_START_TEMP_C:
        print(f"fan_check: refusing — already hot "
              f"(cpu={start.cpu_temp}C gpu={start.gpu_temp}C); cool down first")
        return 1
    print(f">>> driving fan {args.fan} to {TEST_DUTY_PCT}% — "
          "listen: which physical side spins up? <<<", flush=True)
    try:
        fans.set_duty(ec, args.fan, TEST_DUTY_PCT)
        for i in range(SAMPLES):
            time.sleep(1)
            sample(ec, f"  t+{i + 1}s")
    finally:
        # The failsafe is not optional: whatever happened above, firmware
        # gets its fan back.
        fans.restore_auto(ec, args.fan)
        print(f">>> fan {args.fan} handed back to firmware auto <<<", flush=True)
    time.sleep(2)
    sample(ec, "after ")
    return 0


def _uid() -> int:
    for line in pathlib.Path("/proc/self/status").read_text().splitlines():
        if line.startswith("Uid:"):
            return int(line.split()[1])
    return -1


if __name__ == "__main__":
    raise SystemExit(main())
