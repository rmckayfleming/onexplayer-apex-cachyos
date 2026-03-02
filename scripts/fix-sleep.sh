#!/usr/bin/env bash
# fix-sleep.sh — Remove ALL sleep fix kernel parameters for OneXPlayer Apex on Bazzite
#
# S0i3 deep sleep does NOT work on Strix Halo with kernel 6.17 — ACPI C4 support
# is missing until kernel 6.18+. Previous fix attempts applied various kargs that
# either didn't help or caused hangs on sleep.
#
# This script removes ALL previously applied sleep fix kargs and udev rules to
# restore default behavior. Requires a reboot if any kargs were removed.

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

# ---------- 1. Remove ALL sleep fix kernel params ----------

ALL_KARGS=(
    # test/sleep branch
    "iommu=pt"
    "acpi.ec_no_wakeup=1"
    # main branch
    "amd_iommu=off"
    "amd_iommu=on"
    # ancient attempts
    "amdgpu.cwsr_enable=0"
    "amdgpu.gttsize=126976"
    "ttm.pages_limit=32505856"
)

CMDLINE=$(cat /proc/cmdline)
REBOOT_NEEDED=false

info "Checking for sleep fix kernel parameters to remove..."
for karg in "${ALL_KARGS[@]}"; do
    if echo "$CMDLINE" | grep -q "$karg"; then
        info "  Removing: $karg"
        rpm-ostree kargs --delete="$karg" 2>/dev/null || warn "  Could not remove $karg"
        REBOOT_NEEDED=true
    else
        info "  Not present: $karg (skipping)"
    fi
done

# ---------- 2. Remove udev rules ----------

UDEV_RULES=(
    "/etc/udev/rules.d/91-oxp-fingerprint-no-wakeup.rules"
    "/etc/udev/rules.d/99-disable-spurious-wake.rules"
)
RELOAD_UDEV=false

for rule in "${UDEV_RULES[@]}"; do
    if [[ -f "$rule" ]]; then
        info "Removing udev rule: $rule"
        rm -f "$rule"
        RELOAD_UDEV=true
    fi
done

if $RELOAD_UDEV; then
    udevadm control --reload-rules
    info "Reloaded udev rules"
fi

# ---------- 3. Summary ----------

echo ""
info "=== Sleep Fix Cleanup Summary ==="
info "Checked kargs: ${ALL_KARGS[*]}"
info "Checked udev rules: ${UDEV_RULES[*]}"

if $REBOOT_NEEDED; then
    echo ""
    warn "Kernel parameters were removed. A reboot is required."
    warn "Note: button fix patches will need to be re-applied after reboot"
    warn "      (rpm-ostree creates a new ostree deployment)."
    warn "Run: systemctl reboot"
else
    echo ""
    info "No sleep fix kargs found. System is clean."
fi

echo ""
info "S0i3 deep sleep is not supported on Strix Halo until kernel 6.18+."
info "See docs/sleep-research.md for details."
