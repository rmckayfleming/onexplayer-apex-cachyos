"""Sleep/suspend fix for OneXPlayer Apex (Strix Halo) on Bazzite.

Applies kernel parameters via rpm-ostree kargs and creates a udev rule
to disable spurious wake sources.
"""

import logging
import os
import subprocess

logger = logging.getLogger("OXP-SleepFix")

KARGS = [
    "amdgpu.cwsr_enable=0",       # Fix MES firmware hang on resume
    "iommu=pt",                    # Passthrough IOMMU
    "amdgpu.gttsize=126976",       # Increase GTT size for large VRAM
    "ttm.pages_limit=32505856",    # Increase TTM page limit
]

UDEV_RULE_PATH = "/etc/udev/rules.d/99-disable-spurious-wake.rules"
UDEV_RULE_CONTENT = (
    '# Disable fingerprint sensor wake (common cause of spurious wake on OneXPlayer)\n'
    'ACTION=="add", SUBSYSTEM=="i2c", ATTR{name}=="PNP0C50:00", '
    'ATTR{power/wakeup}="disabled"\n'
)

WAKE_SYSFS = "/sys/bus/i2c/devices/i2c-PNP0C50:00/power/wakeup"


def get_status():
    """Check which sleep fixes are applied."""
    try:
        with open("/proc/cmdline") as f:
            cmdline = f.read()
    except Exception:
        cmdline = ""

    kargs_status = {}
    for karg in KARGS:
        kargs_status[karg] = karg in cmdline

    all_kargs = all(kargs_status.values())
    udev_exists = os.path.exists(UDEV_RULE_PATH)

    return {
        "applied": all_kargs and udev_exists,
        "kargs": kargs_status,
        "all_kargs_set": all_kargs,
        "udev_rule": udev_exists,
    }


def apply():
    """Apply sleep fixes. Returns status dict with reboot_needed flag."""
    kargs_changed = False

    try:
        with open("/proc/cmdline") as f:
            cmdline = f.read()
    except Exception:
        cmdline = ""

    # Apply kernel parameters
    for karg in KARGS:
        if karg in cmdline:
            logger.info(f"Already set: {karg}")
            continue
        logger.info(f"Adding karg: {karg}")
        try:
            subprocess.run(
                ["rpm-ostree", "kargs", f"--append-if-missing={karg}"],
                capture_output=True, timeout=60
            )
            kargs_changed = True
        except Exception as e:
            logger.error(f"Failed to add karg {karg}: {e}")
            return {"success": False, "error": f"Failed to add karg {karg}: {e}"}

    # Create udev rule
    if not os.path.exists(UDEV_RULE_PATH):
        try:
            with open(UDEV_RULE_PATH, "w") as f:
                f.write(UDEV_RULE_CONTENT)
            subprocess.run(
                ["udevadm", "control", "--reload-rules"],
                capture_output=True, timeout=10
            )
            logger.info("Created udev rule and reloaded")
        except Exception as e:
            logger.error(f"Failed to create udev rule: {e}")
            return {"success": False, "error": f"Failed to create udev rule: {e}"}

    # Disable wake source for current session
    if os.path.exists(WAKE_SYSFS):
        try:
            with open(WAKE_SYSFS, "w") as f:
                f.write("disabled")
        except Exception:
            pass

    return {
        "success": True,
        "reboot_needed": kargs_changed,
        "message": "Reboot required for kernel params" if kargs_changed else "All fixes applied",
    }
