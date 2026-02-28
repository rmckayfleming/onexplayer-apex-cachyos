#!/usr/bin/env bash
# fix-sleep.sh — Apply sleep/suspend fixes for OneXPlayer Apex (Strix Halo) on Bazzite
# Reference: docs/onexplayer-apex-bazzite-guide.md § 3

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

# ---------- 1. Kernel parameters ----------

KARGS=(
    "amdgpu.cwsr_enable=0"        # Fix MES firmware hang on resume
    "iommu=pt"                     # Passthrough IOMMU — reduces overhead
    "amdgpu.gttsize=126976"        # Increase GTT size for large VRAM configs
    "ttm.pages_limit=32505856"     # Increase TTM page limit
)

info "Applying kernel parameters..."
CMDLINE=$(cat /proc/cmdline)
KARGS_CHANGED=false

for karg in "${KARGS[@]}"; do
    if echo "$CMDLINE" | grep -q "$karg"; then
        info "  Already set: $karg"
    else
        info "  Adding: $karg"
        rpm-ostree kargs --append-if-missing="$karg"
        KARGS_CHANGED=true
    fi
done

# ---------- 2. Disable spurious wake sources ----------

UDEV_RULE="/etc/udev/rules.d/99-disable-spurious-wake.rules"

info "Setting up udev rule to disable spurious wake sources..."

if [[ -f "$UDEV_RULE" ]]; then
    info "  Udev rule already exists at $UDEV_RULE"
else
    cat > "$UDEV_RULE" << 'EOF'
# Disable fingerprint sensor wake (common cause of spurious wake on OneXPlayer)
ACTION=="add", SUBSYSTEM=="i2c", ATTR{name}=="PNP0C50:00", ATTR{power/wakeup}="disabled"
EOF
    info "  Created $UDEV_RULE"
    udevadm control --reload-rules
    info "  Reloaded udev rules"
fi

# Also disable right now for this session
if [[ -e /sys/bus/i2c/devices/i2c-PNP0C50:00/power/wakeup ]]; then
    echo disabled > /sys/bus/i2c/devices/i2c-PNP0C50:00/power/wakeup 2>/dev/null && \
        info "  Disabled fingerprint sensor wake for current session" || \
        warn "  Could not disable fingerprint sensor wake (device path may differ)"
else
    warn "  Fingerprint sensor wake path not found — may not apply to this device"
fi

# ---------- 3. Summary ----------

echo ""
info "=== Sleep Fix Summary ==="
info "Kernel params applied: ${KARGS[*]}"
info "Udev rule: $UDEV_RULE"

if $KARGS_CHANGED; then
    echo ""
    warn "Kernel parameters were changed. A reboot is required."
    warn "Run: systemctl reboot"
else
    echo ""
    info "All kernel parameters were already set. No reboot needed."
fi

echo ""
info "After reboot, verify with:"
info "  cat /proc/cmdline"
echo ""
info "To test suspend:"
info "  sudo systemctl suspend"
info "  # After wake, check for errors:"
info "  journalctl -b | grep -i 'suspend\|resume\|amdgpu\|vpe\|mes\|error\|fail' | tail -30"
