#!/bin/bash
# probe.sh — read-only hardware probe, the first thing to run on a new model.
#
# This script NEVER writes to the EC, sysfs, or any device. It only reads:
# DMI strings, kernel/module state, sysfs listings, hwmon/powercap values,
# nvidia-smi queries, upower info, and — when run as root — raw EC bytes at
# the offsets documented in docs/PROTOCOL.md.
#
# Usage:  ./tools/probe.sh [output-file]
#         sudo ./tools/probe.sh probe.txt   # as root, to include the EC section
set -uo pipefail

OUT=${1:-}
if [ -n "$OUT" ]; then mkdir -p "$(dirname "$OUT")"; exec >"$OUT" 2>&1; fi

section() { printf '\n== %s ==\n' "$*"; }
kv() { printf '%s: %s\n' "$1" "$(cat "$2" 2>/dev/null || echo '<unreadable>')"; }

section "identity"
printf 'date: %s\n' "$(date -Is)"
printf 'kernel: %s\n' "$(uname -r)"
printf 'uid: %s\n' "$(id -u)"
for f in sys_vendor product_name product_version bios_version bios_date board_name; do
  kv "dmi.$f" "/sys/devices/virtual/dmi/id/$f"
done
command -v dmidecode >/dev/null && [ "$(id -u)" = 0 ] && dmidecode -t system 2>/dev/null | sed -n '1,20p' | sed -E 's/(Serial Number|UUID):.*/\1: <redacted>/'

section "modules"
lsmod 2>/dev/null | grep -iE '^(ec_sys|acpi_ec|clevo|tuxedo|gigabyte_wmi|wmi)' || echo '<none of ec_sys/clevo/tuxedo/gigabyte_wmi loaded>'
kv "ec_sys.write_support" /sys/module/ec_sys/parameters/write_support

section "sysfs surfaces"
ls /sys/class/hwmon/ 2>/dev/null || echo '<no hwmon>'
for d in /sys/class/hwmon/hwmon*; do [ -e "$d/name" ] && printf 'hwmon %s: %s\n' "$d" "$(cat "$d/name")"; done
ls -d /sys/firmware/acpi/platform_profile* 2>/dev/null || echo '<no platform_profile>'
ls /sys/class/powercap/ 2>/dev/null || echo '<no powercap>'
for c in /sys/class/powercap/intel-rapl:0/constraint_*_name /sys/class/powercap/intel-rapl:0/constraint_*_power_limit_uw /sys/class/powercap/intel-rapl:0/constraint_*_max_power_uw; do
  [ -e "$c" ] && printf '%s: %s\n' "$c" "$(cat "$c" 2>/dev/null)"
done
ls /sys/class/power_supply/ 2>/dev/null || echo '<no power_supply>'
for b in /sys/class/power_supply/BAT*; do
  [ -e "$b" ] || continue
  printf 'battery %s\n' "$b"; ls "$b" | grep -E 'charge_control|charge_behaviour' || echo '  <no charge_control_* files>'
done

section "sensors (read-only)"
command -v sensors >/dev/null && sensors 2>/dev/null | sed -n '1,40p' || echo '<lm-sensors not available>'

section "nvidia (read-only)"
if command -v nvidia-smi >/dev/null; then
  nvidia-smi --query-gpu=name,power.default_limit,power.min_limit,power.max_limit,temperature.gpu --format=csv 2>&1
  nvidia-smi -q -d POWER 2>/dev/null | grep -iE 'power limit|power draw|throttle' | sed -n '1,15p'
else echo '<nvidia-smi not available>'; fi

section "upower (read-only)"
command -v upower >/dev/null && upower -i "$(upower -e 2>/dev/null | grep -i bat | head -n1)" 2>/dev/null | grep -iE 'state|percentage|energy|charge' | sed -n '1,12p' || echo '<upower/battery not available>'

section "ec registers (root only, read-only)"
EC_IO=/sys/kernel/debug/ec/ec0/io
if [ "$(id -u)" != 0 ]; then
  echo '<skipped: re-run with sudo to read EC bytes>'
elif [ ! -r "$EC_IO" ]; then
  echo "<$EC_IO not readable: ec_sys debugfs unavailable on this kernel>"
  # Fedora stock kernels build ACPI EC in without the debugfs interface, so
  # fall back to /dev/port through the daemon's own telemetry read, which
  # holds the daemon's EC lock. Reads only — this probe never writes.
  SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
  if [ -e /dev/port ] && command -v python3 >/dev/null; then
    python3 "$SCRIPT_DIR/ec_read.py" || echo '<ec_read.py via /dev/port failed>'
  else
    echo '</dev/port unavailable: no stock-kernel EC path on this machine>'
  fi
else
  # offset:label pairs from docs/PROTOCOL.md (verified on G6 KF; check on this model)
  for pair in 07:cpu_temp 0A:gpu_temp_a 0B:gpu_temp_b CE:dut1 CF:dut2 D0:tach1_hi D1:tach1_lo D2:tach2_hi D3:tach2_lo; do
    off=${pair%%:*}; label=${pair##*:}
    val=$(dd if="$EC_IO" bs=1 skip=$((16#$off)) count=1 2>/dev/null | od -An -tu1 | tr -d ' ')
    printf 'ec[0x%s] %-10s = %s\n' "$off" "$label" "${val:-<read failed>}"
  done
fi

section "done"
echo "probe complete (no writes were performed)"
