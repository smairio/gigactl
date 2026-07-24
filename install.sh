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
        /usr/lib/gigactl/gigactl-cli-lib.sh \
        /etc/systemd/system/gkbd-restore.service \
        /lib/systemd/system-sleep/gkbd
  rmdir /usr/lib/gigactl 2>/dev/null || true  # the .deb may own it too
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
# The plumbing both scripts source. They look next to themselves first, which
# fails for these /usr/local/bin copies, then fall back to this path (the same
# one the .deb uses).
install -d /usr/lib/gigactl
install -m 644 "$DIR/gigactl-cli-lib.sh" /usr/lib/gigactl/gigactl-cli-lib.sh
install -m 644 "$DIR/systemd/gkbd-restore.service" /etc/systemd/system/gkbd-restore.service
install -m 755 "$DIR/systemd/system-sleep-gkbd" /lib/systemd/system-sleep/gkbd
systemctl daemon-reload
systemctl enable gkbd-restore.service >/dev/null 2>&1

# --- desktop integration for the GUI -----------------------------------------
# The .deb (dpkg-buildpackage -us -uc -b) owns all of this properly and installs
# the daemon too — prefer it. Installing these by hand is what makes the launcher,
# the tray icon's themed name and login autostart work from a source checkout.
# The icon goes into hicolor as a scalable app icon so both the desktop entries'
# Icon= and the tray's IconName resolve to it.
install -d /usr/share/icons/hicolor/scalable/apps
install -m 644 "$DIR/design/icon.svg" \
  /usr/share/icons/hicolor/scalable/apps/io.github.smairio.gigactl.svg
gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true

# The entries above are dead without the binary they name: systemd's XDG
# autostart generator skips any entry whose Exec= is not on PATH. This wrapper
# runs the GUI straight out of this checkout; the .deb ships a real entry point
# at /usr/bin/gigactl-gui instead. NOTE: this script does not install the daemon
# — build the .deb for that, or run it from daemon/ by hand.
install -d /usr/local/bin
cat > /usr/local/bin/gigactl-gui <<WRAPPER
#!/bin/sh
# installed by gigactl install.sh from $DIR
exec env PYTHONPATH="$DIR/gui" python3 -m gigactl_gui "\$@"
WRAPPER
chmod 755 /usr/local/bin/gigactl-gui

install -d /usr/share/applications /etc/xdg/autostart
install -m 644 "$DIR/gui/data/io.github.smairio.gigactl.desktop" \
  /usr/share/applications/io.github.smairio.gigactl.desktop
install -m 644 "$DIR/gui/data/gigactl-tray.desktop" \
  /etc/xdg/autostart/gigactl-tray.desktop
update-desktop-database -q /usr/share/applications 2>/dev/null || true

echo
echo "gigactl installed successfully."
echo "  gfan          # fan status"
echo "  gfan 60       # both fans 60%"
echo "  gfan auto     # firmware control"
echo "  gkbd red      # keyboard color"
echo "  gkbd brightness 50"
