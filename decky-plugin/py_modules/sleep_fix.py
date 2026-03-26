"""Light sleep (s2idle) kargs manager for OneXPlayer Apex on CachyOS.

Light sleep works on Strix Halo when "ACPI Auto configuration" is enabled
in the BIOS. This module applies the required kernel parameters and removes
any known-problematic legacy kargs from previous fix attempts.

Uses systemd-boot via /etc/kernel/cmdline and reinstall-kernels.
"""

import logging
import os
import shutil
import subprocess

logger = logging.getLogger("OXP-SleepFix")

_log_info_cb = None
_log_error_cb = None
_log_warning_cb = None


def set_log_callbacks(info_fn, error_fn, warning_fn):
    global _log_info_cb, _log_error_cb, _log_warning_cb
    _log_info_cb = info_fn
    _log_error_cb = error_fn
    _log_warning_cb = warning_fn


def _log_info(msg):
    if _log_info_cb:
        _log_info_cb(msg)
    else:
        logger.info(msg)


def _log_error(msg):
    if _log_error_cb:
        _log_error_cb(msg)
    else:
        logger.error(msg)


def _log_warning(msg):
    if _log_warning_cb:
        _log_warning_cb(msg)
    else:
        logger.warning(msg)


def _clean_env():
    """Return a subprocess environment without PyInstaller's LD_LIBRARY_PATH."""
    env = os.environ.copy()
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        env.pop(var, None)
    return env


_CMDLINE_PATH = "/etc/kernel/cmdline"

# Kargs that enable light sleep (s2idle)
LIGHT_SLEEP_KARGS = [
    "mem_sleep_default=s2idle",
    "amd_iommu=off",             # Required — IOMMU must be off for sleep on Strix Halo
]

# Legacy kargs that should be removed — broken or counterproductive
PROBLEMATIC_KARGS = [
    "amd_iommu=on",              # Invalid AMD parameter, silently ignored
    "acpi.ec_no_wakeup=1",       # Prevents EC-based wakeup
    "amdgpu.cwsr_enable=0",      # Compute-specific, not needed for sleep
    "amdgpu.gttsize=126976",     # Not sleep-related
    "ttm.pages_limit=32505856",  # Not sleep-related
]


def _read_cmdline():
    try:
        with open("/proc/cmdline") as f:
            return f.read()
    except Exception:
        return ""


def _read_kernel_cmdline_file():
    """Read the persistent kernel cmdline from /etc/kernel/cmdline."""
    try:
        with open(_CMDLINE_PATH) as f:
            return f.read().strip()
    except Exception:
        return ""


def _write_kernel_cmdline(content):
    """Write updated kernel cmdline and regenerate boot entries."""
    # Back up existing file
    if os.path.exists(_CMDLINE_PATH):
        shutil.copy2(_CMDLINE_PATH, _CMDLINE_PATH + ".bak")

    with open(_CMDLINE_PATH, "w") as f:
        f.write(content + "\n")

    # Regenerate boot entries with new cmdline
    r = subprocess.run(
        ["reinstall-kernels"],
        capture_output=True, text=True, timeout=120,
        env=_clean_env()
    )
    if r.returncode != 0:
        raise RuntimeError(f"reinstall-kernels failed: {r.stderr.strip()}")


def get_status():
    """Check light sleep kargs and problematic legacy kargs."""
    cmdline = _read_cmdline()

    light_sleep_present = [k for k in LIGHT_SLEEP_KARGS if k in cmdline]
    light_sleep_missing = [k for k in LIGHT_SLEEP_KARGS if k not in cmdline]
    problematic_found = [k for k in PROBLEMATIC_KARGS if k in cmdline]

    applied = len(light_sleep_missing) == 0 and len(problematic_found) == 0

    return {
        "applied": applied,
        "light_sleep_present": light_sleep_present,
        "light_sleep_missing": light_sleep_missing,
        "problematic_kargs": problematic_found,
        "has_problematic_kargs": len(problematic_found) > 0,
    }


def apply():
    """Apply light sleep kargs and remove problematic legacy kargs.

    Edits /etc/kernel/cmdline and runs reinstall-kernels for systemd-boot.
    """
    cmdline = _read_cmdline()
    steps = []
    changes_needed = False

    _log_info("=== Light Sleep Apply Start ===")

    # Read the persistent cmdline file (what systemd-boot uses)
    persistent = _read_kernel_cmdline_file()
    params = persistent.split()

    # Add missing sleep kargs
    for karg in LIGHT_SLEEP_KARGS:
        if karg not in persistent:
            params.append(karg)
            steps.append(f"Adding {karg}")
            changes_needed = True

    # Remove problematic kargs
    for karg in PROBLEMATIC_KARGS:
        if karg in persistent:
            params = [p for p in params if p != karg]
            steps.append(f"Removing {karg}")
            changes_needed = True

    if not changes_needed:
        _log_info("Light sleep kargs already correct, no changes needed")
        return {
            "success": True,
            "reboot_needed": False,
            "message": "Light sleep kargs already applied. No changes needed.",
            "steps": ["All kargs already correct"],
        }

    new_cmdline = " ".join(params)
    _log_info(f"New cmdline: {new_cmdline}")

    try:
        _write_kernel_cmdline(new_cmdline)
        steps.append("Updated /etc/kernel/cmdline")
        steps.append("Regenerated boot entries")
    except Exception as e:
        _log_error(f"Failed to update kernel cmdline: {e}")
        return {
            "success": False,
            "error": str(e),
            "steps": steps,
        }

    msg = "Light sleep kargs applied. Reboot required."
    _log_info(f"Light sleep apply complete: {msg}")
    return {
        "success": True,
        "reboot_needed": True,
        "message": msg,
        "steps": steps,
    }


def revert():
    """Remove light sleep kargs."""
    steps = []
    changes_needed = False

    _log_info("=== Light Sleep Revert Start ===")

    persistent = _read_kernel_cmdline_file()
    params = persistent.split()

    for karg in LIGHT_SLEEP_KARGS:
        if karg in persistent:
            params = [p for p in params if p != karg]
            steps.append(f"Removing {karg}")
            changes_needed = True

    if not changes_needed:
        _log_info("No light sleep kargs to remove")
        return {
            "success": True,
            "reboot_needed": False,
            "message": "No light sleep kargs to remove.",
            "steps": ["No kargs to remove"],
        }

    new_cmdline = " ".join(params)
    _log_info(f"New cmdline: {new_cmdline}")

    try:
        _write_kernel_cmdline(new_cmdline)
        steps.append("Updated /etc/kernel/cmdline")
        steps.append("Regenerated boot entries")
    except Exception as e:
        _log_error(f"Failed to update kernel cmdline: {e}")
        return {
            "success": False,
            "error": str(e),
            "steps": steps,
        }

    msg = "Light sleep kargs removed. Reboot required."
    _log_info(f"Light sleep revert complete: {msg}")
    return {
        "success": True,
        "reboot_needed": True,
        "message": msg,
        "steps": steps,
    }


# Legacy compat — old frontend called remove() for cleanup
def remove():
    """Remove problematic kargs (legacy compat, delegates to apply)."""
    return apply()
