"""Sleep enablement for OneXPlayer Apex on Bazzite.

The fw-fanctrl-suspend script at /usr/lib/systemd/system-sleep/fw-fanctrl-suspend
is a Framework Laptop tool shipped with Bazzite that errors on non-Framework
hardware, keeping fans running during sleep. This module neutralizes it.

Also installs a fingerprint reader wake fix udev rule to prevent the
fingerprint sensor from waking the device immediately after sleep.

Requires ostree unlock since Bazzite is immutable.
"""

import hashlib
import logging
import os
import subprocess
import time

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


_FW_SCRIPT = "/usr/lib/systemd/system-sleep/fw-fanctrl-suspend"
_FINGERPRINT_RULE = "/etc/udev/rules.d/91-oxp-fingerprint-no-wakeup.rules"
_FINGERPRINT_RULE_CONTENT = (
    '# Disable fingerprint reader as wake source (prevents immediate wake after sleep)\n'
    'ACTION=="add", SUBSYSTEM=="usb", DRIVERS=="usb", '
    'ATTRS{idVendor}=="10a5", ATTRS{idProduct}=="9800", '
    'ATTR{power/wakeup}="disabled"\n'
)

_NOOP_SCRIPT = "#!/bin/bash\n# Neutralized by OXP Apex Tools (was fw-fanctrl-suspend for Framework Laptop)\nexit 0\n"


def _is_filesystem_writable(test_path):
    """Check if the immutable filesystem is writable."""
    test_dir = os.path.dirname(test_path)
    probe = os.path.join(test_dir, ".oxp_write_test")
    try:
        with open(probe, "w") as f:
            f.write("test")
        os.remove(probe)
        return True
    except OSError:
        return False


def _unlock_filesystem(test_path, steps):
    """Unlock the ostree immutable filesystem with retries."""
    _log_info("Unlocking filesystem...")

    if _is_filesystem_writable(test_path):
        _log_info("Filesystem already writable")
        steps.append("Filesystem already writable")
        return True

    try:
        r = subprocess.run(
            ["ostree", "admin", "unlock", "--hotfix"],
            capture_output=True, text=True, timeout=120,
            env=_clean_env()
        )
        if r.returncode == 0:
            steps.append("Unlocked filesystem")
        else:
            steps.append(f"ostree unlock returned {r.returncode} (may already be unlocked)")
    except subprocess.TimeoutExpired:
        steps.append("ostree unlock timed out")
        return False
    except Exception as e:
        steps.append(f"ostree unlock failed: {e}")
        return False

    # Wait for writable
    for attempt in range(1, 7):
        if _is_filesystem_writable(test_path):
            steps.append("Filesystem confirmed writable")
            return True
        wait = min(attempt * 0.5, 2.0)
        time.sleep(wait)

    steps.append("Filesystem not writable after retries")
    return False


def _is_script_neutralized():
    """Check if fw-fanctrl-suspend is a no-op."""
    if not os.path.exists(_FW_SCRIPT):
        return True  # doesn't exist = not a problem
    try:
        with open(_FW_SCRIPT) as f:
            content = f.read()
        # Check if it's our no-op or effectively empty
        stripped = content.strip()
        if stripped == "" or stripped.endswith("exit 0"):
            return True
        # Check by hash of our specific no-op
        if content == _NOOP_SCRIPT:
            return True
        # Check file size — the real script is much larger than our no-op
        if len(content) < 100:
            return True
        return False
    except OSError:
        return True


def _fingerprint_rule_installed():
    """Check if the fingerprint wake fix udev rule is installed."""
    return os.path.exists(_FINGERPRINT_RULE)


def is_applied():
    """Check current status of sleep enablement fixes."""
    script_neutralized = _is_script_neutralized()
    fingerprint_rule = _fingerprint_rule_installed()
    fw_script_exists = os.path.exists(_FW_SCRIPT)

    applied = script_neutralized and fingerprint_rule

    return {
        "applied": applied,
        "fw_script_neutralized": script_neutralized,
        "fw_script_exists": fw_script_exists,
        "fingerprint_rule_installed": fingerprint_rule,
    }


def apply():
    """Neutralize fw-fanctrl-suspend and install fingerprint wake fix."""
    steps = []
    _log_info("=== Sleep Enable Apply Start ===")

    status = is_applied()
    if status.get("applied"):
        return {"success": True, "message": "Already applied", "steps": ["Already applied"]}

    # Neutralize fw-fanctrl-suspend if it exists and isn't already a no-op
    if os.path.exists(_FW_SCRIPT) and not status.get("fw_script_neutralized"):
        # Need ostree unlock for /usr/lib
        if not _unlock_filesystem(_FW_SCRIPT, steps):
            return {"success": False, "error": "Filesystem not writable. ostree unlock failed.", "steps": steps}

        try:
            # Back up original by saving its hash for logging
            with open(_FW_SCRIPT, "rb") as f:
                orig_hash = hashlib.sha256(f.read()).hexdigest()[:12]
            _log_info(f"Original fw-fanctrl-suspend hash: {orig_hash}")

            with open(_FW_SCRIPT, "w") as f:
                f.write(_NOOP_SCRIPT)
            os.chmod(_FW_SCRIPT, 0o755)
            steps.append("Neutralized fw-fanctrl-suspend")
            _log_info("fw-fanctrl-suspend neutralized")
        except Exception as e:
            return {"success": False, "error": f"Failed to neutralize script: {e}", "steps": steps}
    elif not os.path.exists(_FW_SCRIPT):
        steps.append("fw-fanctrl-suspend not present (OK)")
    else:
        steps.append("fw-fanctrl-suspend already neutralized")

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
    """Revert sleep enablement fixes.

    Note: the fw-fanctrl-suspend script will be restored to its original
    content on the next Bazzite/ostree update. We just remove our no-op
    so ostree's next deployment restores the original.
    """
    steps = []
    _log_info("=== Sleep Enable Revert Start ===")

    # For fw-fanctrl-suspend: we can't easily restore the original since
    # it's on the immutable filesystem. But ostree will restore it on next
    # update. We'll just note this.
    if os.path.exists(_FW_SCRIPT):
        try:
            with open(_FW_SCRIPT) as f:
                content = f.read()
            if content == _NOOP_SCRIPT:
                steps.append("fw-fanctrl-suspend will be restored on next Bazzite update")
                _log_info("fw-fanctrl-suspend is our no-op — will restore on update")
        except Exception:
            pass

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
        "message": "Sleep fixes reverted (fw-fanctrl-suspend restores on next update)",
        "steps": steps,
    }
