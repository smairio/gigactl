#!/bin/bash
# gigactl installer — fan + keyboard backlight control for Gigabyte G5/G6 laptops
set -euo pipefail

NBFC_DEB_URL="https://github.com/nbfc-linux/nbfc-linux/releases/download/0.5.2/ubuntu-noble-nbfc-linux_0.5.2_amd64.deb"
DIR="$(cd "$(dirname "$0")" && pwd)"

die() { echo "install: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "run as root: sudo ./install.sh"

if [ "${1:-}" = "--uninstall" ]; then
  systemctl disable --now gkbd-restore.service 2>/dev/null || true
  rm -f /usr/local/bin/gfan /usr/local/bin/gkbd \
        /etc/systemd/system/gkbd-restore.service \
        /lib/systemd/system-sleep/gkbd
  rm -rf /var/lib/gkbd
  systemctl daemon-reload
  echo "gigactl uninstalled. (ec_probe/nbfc-linux left in place; remove with: apt remove nbfc-linux)"
  exit 0
fi

# --- hardware check -----------------------------------------------------------
VENDOR=$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || true)
MODEL=$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)
case "$VENDOR" in
  GIGABYTE*|Gigabyte*) ;;
  *) die "this machine is '$VENDOR $MODEL' — gigactl supports Gigabyte G5/G6 laptops only." ;;
esac
case "$MODEL" in
  "G6 KF") echo "Detected: Gigabyte G6 KF (verified model)" ;;
  G5*|G6*|G7*) echo "Detected: Gigabyte $MODEL — untested but same platform; tools will verify each write." ;;
  *) die "model '$MODEL' is not a G5/G6/G7 — refusing to install." ;;
esac

# --- dependency: ec_probe -----------------------------------------------------
if ! command -v ec_probe >/dev/null; then
  echo "Installing nbfc-linux (provides ec_probe)..."
  tmp=$(mktemp -d)
  curl -sLo "$tmp/nbfc.deb" "$NBFC_DEB_URL" || die "download failed"
  apt-get install -y "$tmp/nbfc.deb" || die "nbfc-linux install failed"
  rm -rf "$tmp"
fi

# --- install ------------------------------------------------------------------
install -m 755 "$DIR/gfan" /usr/local/bin/gfan
install -m 755 "$DIR/gkbd" /usr/local/bin/gkbd
install -m 644 "$DIR/systemd/gkbd-restore.service" /etc/systemd/system/gkbd-restore.service
install -m 755 "$DIR/systemd/system-sleep-gkbd" /lib/systemd/system-sleep/gkbd
systemctl daemon-reload
systemctl enable gkbd-restore.service >/dev/null 2>&1

echo
echo "gigactl installed successfully."
echo "  gfan          # fan status"
echo "  gfan 60       # both fans 60%"
echo "  gfan auto     # firmware control"
echo "  gkbd red      # keyboard color"
echo "  gkbd brightness 50"
