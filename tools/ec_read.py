#!/usr/bin/env python3
"""Read the temperature and fan registers three times. It never writes.

    sudo python3 tools/ec_read.py

Each sample is the daemon's own telemetry read (gigactld.telemetry), so the
registers are decoded exactly as gigactld decodes them, and all of them are
read inside one EmbeddedController.transaction(). That holds the EC lock the
daemon and gfan take, so a sample never interleaves with their commands.

The EC backend is picked the way the daemon picks it: the ec_sys debugfs file
when the kernel has it, /dev/port otherwise. Fedora's stock kernel has no
ec_sys, so there it is /dev/port.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "daemon"))

from gigactld.ec import EcError, EmbeddedController  # noqa: E402
from gigactld.telemetry import read_telemetry  # noqa: E402

SAMPLES = 3


def main(ec=None) -> int:
    if ec is None:
        ec = EmbeddedController()
    for n in range(SAMPLES):
        try:
            t = read_telemetry(ec)
        except (EcError, OSError) as exc:
            print(f"ec_read: read failed on sample {n}: {exc}")
            return 1
        print(f"sample {n}: cpu={t.cpu_temp}C gpu={t.gpu_temp}C "
              f"dut1={t.fan1_duty_pct}% dut2={t.fan2_duty_pct}% "
              f"rpm1~{t.fan1_rpm} rpm2~{t.fan2_rpm}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
