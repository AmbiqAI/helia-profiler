set -euo pipefail

dry_run=false
if [[ "${1:-}" == "--dry-run" ]]; then
  dry_run=true
elif [[ $# -ne 0 ]]; then
  echo "Usage: nix run .#install-udev-rules [-- --dry-run]" >&2
  exit 2
fi

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "The udev setup is only needed on Linux." >&2
  exit 1
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

cat >"$tmp_dir/99-segger-jlink.rules" <<'EOF'
# SEGGER J-Link probes. Grant the active local session access.
SUBSYSTEM=="usb", ATTR{idVendor}=="1366", MODE="0660", TAG+="uaccess", ENV{ID_MM_DEVICE_IGNORE}="1"
EOF

cat >"$tmp_dir/99-joulescope.rules" <<'EOF'
# Joulescope instruments. Grant the active local session access.
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="16d0", ATTRS{idProduct}=="0e88", MODE="0660", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="16d0", ATTRS{idProduct}=="0e87", MODE="0660", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="16d0", ATTRS{idProduct}=="10ba", MODE="0660", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="16d0", ATTRS{idProduct}=="10b9", MODE="0660", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="16d0", ATTRS{idProduct}=="135a", MODE="0660", TAG+="uaccess"
EOF

if [[ "$dry_run" == true ]]; then
  echo "Would install /etc/udev/rules.d/99-segger-jlink.rules:"
  cat "$tmp_dir/99-segger-jlink.rules"
  echo
  echo "Would install /etc/udev/rules.d/99-joulescope.rules:"
  cat "$tmp_dir/99-joulescope.rules"
  exit 0
fi

echo "Installing J-Link and Joulescope USB access rules with sudo..."
sudo install -m 0644 \
  "$tmp_dir/99-segger-jlink.rules" \
  /etc/udev/rules.d/99-segger-jlink.rules
sudo install -m 0644 \
  "$tmp_dir/99-joulescope.rules" \
  /etc/udev/rules.d/99-joulescope.rules
sudo udevadm control --reload-rules
sudo udevadm trigger

echo "USB rules installed. Unplug and reconnect the probes before testing."
