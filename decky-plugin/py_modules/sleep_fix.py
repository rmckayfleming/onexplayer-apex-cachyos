"""Sleep/suspend fix for OneXPlayer Apex (Strix Halo) on Bazzite.

The Apex uses an AMD Strix Halo APU which has several known issues with
suspend/resume on current kernel/firmware versions:

1. MES firmware hang on resume — fixed by disabling CWSR (amdgpu.cwsr_enable=0)
2. IOMMU overhead causing delays — fixed with passthrough mode (iommu=pt)
3. GTT/TTM memory limits too low for the large VRAM — increased via kernel params
4. Spurious wake from fingerprint sensor — disabled via udev rule

This module applies kernel parameters via rpm-ostree (Bazzite's atomic update
system) and creates a udev rule. Kernel params require a reboot to take effect.
"""

import logging
import os
import subprocess

logger = logging.getLogger("OXP-SleepFix")


def _clean_env():
    """Return a subprocess environment without PyInstaller's LD_LIBRARY_PATH."""
    env = os.environ.copy()
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        env.pop(var, None)
    return env

# Kernel parameters to append via rpm-ostree kargs.
# These persist across reboots but are lost on Bazzite OS updates.
KARGS = [
    "amdgpu.cwsr_enable=0",       # Disable Compute Wave Save/Restore — fixes MES hang on resume
    "iommu=pt",                    # IOMMU passthrough — reduces overhead, fixes some DMA issues
    "amdgpu.gttsize=126976",       # Increase Graphics Translation Table size for large VRAM configs
    "ttm.pages_limit=32505856",    # Increase Translation Table Manager page limit
]

# Udev rule to disable wake-on-touch from the fingerprint sensor.
# Without this, the device often wakes immediately after entering suspend.
UDEV_RULE_PATH = "/etc/udev/rules.d/99-disable-spurious-wake.rules"
UDEV_RULE_CONTENT = (
    '# Disable fingerprint sensor wake (common cause of spurious wake on OneXPlayer)\n'
    'ACTION=="add", SUBSYSTEM=="i2c", ATTR{name}=="PNP0C50:00", '
    'ATTR{power/wakeup}="disabled"\n'
)

# Sysfs path to disable wake source for the current session (without reboot)
WAKE_SYSFS = "/sys/bus/i2c/devices/i2c-PNP0C50:00/power/wakeup"


def get_status():
    """Check which sleep fixes are currently applied.

    Reads /proc/cmdline to check kernel params and checks if the
    udev rule file exists.
    """
    try:
        with open("/proc/cmdline") as f:
            cmdline = f.read()
    except Exception:
        cmdline = ""

    # Check each kernel param individually
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
    """Apply all sleep fixes. Returns status dict with reboot_needed flag.

    Uses rpm-ostree to add kernel params (requires reboot) and creates
    a udev rule to disable spurious wake sources (takes effect immediately
    after udevadm reload).
    """
    kargs_changed = False

    try:
        with open("/proc/cmdline") as f:
            cmdline = f.read()
    except Exception:
        cmdline = ""

    # Apply kernel parameters via rpm-ostree
    for karg in KARGS:
        if karg in cmdline:
            logger.info(f"Already set: {karg}")
            continue
        logger.info(f"Adding karg: {karg}")
        try:
            # --append-if-missing is idempotent — safe to re-run
            subprocess.run(
                ["rpm-ostree", "kargs", f"--append-if-missing={karg}"],
                capture_output=True, timeout=60,
                env=_clean_env()
            )
            kargs_changed = True
        except Exception as e:
            logger.error(f"Failed to add karg {karg}: {e}")
            return {"success": False, "error": f"Failed to add karg {karg}: {e}"}

    # Create udev rule to disable fingerprint sensor wake
    if not os.path.exists(UDEV_RULE_PATH):
        try:
            with open(UDEV_RULE_PATH, "w") as f:
                f.write(UDEV_RULE_CONTENT)
            # Reload udev rules so the new rule takes effect without reboot
            subprocess.run(
                ["udevadm", "control", "--reload-rules"],
                capture_output=True, timeout=10,
                env=_clean_env()
            )
            logger.info("Created udev rule and reloaded")
        except Exception as e:
            logger.error(f"Failed to create udev rule: {e}")
            return {"success": False, "error": f"Failed to create udev rule: {e}"}

    # Also disable wake source immediately for the current boot session
    # (the udev rule only applies on device add, which already happened)
    if os.path.exists(WAKE_SYSFS):
        try:
            with open(WAKE_SYSFS, "w") as f:
                f.write("disabled")
        except Exception:
            pass  # non-critical — the udev rule will handle future boots

    return {
        "success": True,
        "reboot_needed": kargs_changed,
        "message": "Reboot required for kernel params" if kargs_changed else "All fixes applied",
    }
