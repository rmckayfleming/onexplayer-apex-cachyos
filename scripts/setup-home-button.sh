#!/usr/bin/env bash
# setup-home-button.sh — Install Home button → HHD UI launcher as a systemd service
#
# This sets up a background service that monitors the OneXFly Apex's Home/Orange
# button via hidraw and opens HHD's web UI (localhost:5335) when pressed.
#
# The service runs as root (hidraw needs permissions) and persists across reboots.
# Unlike the HHD patches, this survives Bazzite updates since it lives in /etc.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (sudo)."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_SCRIPT="$SCRIPT_DIR/home-button-hhd.py"

if [[ ! -f "$SOURCE_SCRIPT" ]]; then
    error "home-button-hhd.py not found in $SCRIPT_DIR"
    exit 1
fi

# Install the monitor script
INSTALL_DIR="/usr/local/bin"
INSTALL_PATH="$INSTALL_DIR/home-button-hhd"

info "Installing monitor script to $INSTALL_PATH..."
mkdir -p "$INSTALL_DIR"
cp "$SOURCE_SCRIPT" "$INSTALL_PATH"
chmod +x "$INSTALL_PATH"

# Create udev rule to set hidraw permissions for the Apex keyboard HID
info "Creating udev rule for hidraw permissions..."
cat > /etc/udev/rules.d/99-oxp-apex-hidraw.rules << 'EOF'
# OneXFly Apex keyboard HID — allow read access for home button monitor
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="fe00", MODE="0644"
EOF

udevadm control --reload-rules
udevadm trigger --subsystem-match=hidraw 2>/dev/null || true

# Create systemd service
info "Creating systemd service..."
cat > /etc/systemd/system/home-button-hhd.service << EOF
[Unit]
Description=OneXFly Apex Home Button → HHD UI Launcher
After=multi-user.target hhd.service
Wants=hhd.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 $INSTALL_PATH
Restart=on-failure
RestartSec=5
# Run as the deck user so xdg-open works with their desktop session
User=deck
# But we need hidraw access — the udev rule handles permissions
Environment=DISPLAY=:0
Environment=WAYLAND_DISPLAY=wayland-0
Environment=XDG_RUNTIME_DIR=/run/user/1000

[Install]
WantedBy=multi-user.target
EOF

# Enable and start
systemctl daemon-reload
systemctl enable home-button-hhd.service
systemctl restart home-button-hhd.service

sleep 2

if systemctl is-active --quiet home-button-hhd.service; then
    info "Service is running!"
else
    warn "Service may not have started. Check: journalctl -u home-button-hhd -f"
fi

echo ""
info "Setup complete. Press the Home/Orange button to open HHD UI."
info ""
info "Useful commands:"
info "  journalctl -u home-button-hhd -f     # watch logs"
info "  systemctl status home-button-hhd      # check status"
info "  systemctl stop home-button-hhd        # stop"
info "  systemctl disable home-button-hhd     # disable on boot"
info ""
info "To change the launch command, edit: $INSTALL_PATH"
info "  Look for DEFAULT_CMD near the top of the file."
