"""Sleep enablement for OneXPlayer Apex on CachyOS.

Installs a udev rule to prevent the fingerprint reader from waking the
device immediately after sleep.

The fw-fanctrl-suspend fix from the Bazzite version is not needed on
CachyOS as it doesn't ship the Framework Laptop fan control script.
"""

import logging
import os
import subprocess

logger = logging.getLogger("OXP-SleepEnable")

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
    env = os.environ.copy()
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        env.pop(var, None)
    return env


_FINGERPRINT_RULE = "/etc/udev/rules.d/91-oxp-fingerprint-no-wakeup.rules"
_FINGERPRINT_RULE_CONTENT = (
    '# Disable fingerprint reader as wake source (prevents immediate wake after sleep)\n'
    'ACTION=="add", SUBSYSTEM=="usb", DRIVERS=="usb", '
    'ATTRS{idVendor}=="10a5", ATTRS{idProduct}=="9800", '
    'ATTR{power/wakeup}="disabled"\n'
)


def _fingerprint_rule_installed():
    """Check if the fingerprint wake fix udev rule is installed."""
    return os.path.exists(_FINGERPRINT_RULE)


def is_applied():
    """Check current status of sleep enablement fixes."""
    fingerprint_rule = _fingerprint_rule_installed()

    return {
        "applied": fingerprint_rule,
        "fw_script_neutralized": True,  # Not applicable on CachyOS
        "fw_script_exists": False,
        "fingerprint_rule_installed": fingerprint_rule,
    }


def apply():
    """Install fingerprint wake fix udev rule."""
    steps = []
    _log_info("=== Sleep Enable Apply Start ===")

    status = is_applied()
    if status.get("applied"):
        return {"success": True, "message": "Already applied", "steps": ["Already applied"]}

    # Install fingerprint wake fix
    if not _fingerprint_rule_installed():
        try:
            os.makedirs(os.path.dirname(_FINGERPRINT_RULE), exist_ok=True)
            with open(_FINGERPRINT_RULE, "w") as f:
                f.write(_FINGERPRINT_RULE_CONTENT)
            steps.append("Installed fingerprint wake fix udev rule")
            _log_info(f"Created {_FINGERPRINT_RULE}")

            # Reload udev
            subprocess.run(
                ["udevadm", "control", "--reload-rules"],
                capture_output=True, timeout=10, env=_clean_env()
            )
            subprocess.run(
                ["udevadm", "trigger"],
                capture_output=True, timeout=10, env=_clean_env()
            )
            steps.append("Reloaded udev rules")
        except Exception as e:
            _log_warning(f"Failed to install fingerprint rule: {e}")
            steps.append(f"Fingerprint rule failed: {e}")
    else:
        steps.append("Fingerprint wake fix already installed")

    _log_info("Sleep enable applied successfully")
    return {"success": True, "message": "Sleep fixes applied", "steps": steps}


def revert():
    """Revert sleep enablement fixes."""
    steps = []
    _log_info("=== Sleep Enable Revert Start ===")

    # Remove fingerprint rule
    if os.path.exists(_FINGERPRINT_RULE):
        try:
            os.remove(_FINGERPRINT_RULE)
            steps.append("Removed fingerprint wake fix udev rule")
            _log_info(f"Removed {_FINGERPRINT_RULE}")

            subprocess.run(
                ["udevadm", "control", "--reload-rules"],
                capture_output=True, timeout=10, env=_clean_env()
            )
        except Exception as e:
            _log_warning(f"Failed to remove fingerprint rule: {e}")
    else:
        steps.append("Fingerprint rule not present")

    _log_info("Sleep enable reverted")
    return {
        "success": True,
        "message": "Sleep fixes reverted",
        "steps": steps,
    }
