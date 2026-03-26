"""InputPlumber device profile manager for OneXPlayer Apex on CachyOS.

Installs an InputPlumber composite device profile and capability map so
that InputPlumber recognizes the Apex and correctly maps its buttons,
back paddles, and special keys.

Replaces the HHD patching approach used on Bazzite. On CachyOS, the
gamepad daemon is InputPlumber, and device profiles live in
/usr/share/inputplumber/devices/ and /usr/share/inputplumber/capability_maps/.
"""

import hashlib
import logging
import os
import shutil
import subprocess

logger = logging.getLogger("OXP-ButtonFix")

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


# Paths
_PLUGIN_DIR = os.path.dirname(os.path.dirname(__file__))
_INPUTPLUMBER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "inputplumber")

# Source files bundled with the plugin
_DEVICE_PROFILE_SRC = os.path.join(_INPUTPLUMBER_DIR, "50-onexplayer_apex.yaml")
_CAPABILITY_MAP_SRC = os.path.join(_INPUTPLUMBER_DIR, "onexplayer_apex.yaml")

# Install targets
_DEVICE_PROFILE_DST = "/usr/share/inputplumber/devices/50-onexplayer_apex.yaml"
_CAPABILITY_MAP_DST = "/usr/share/inputplumber/capability_maps/onexplayer_apex.yaml"

_SERVICE_NAME = "inputplumber.service"


def _file_hash(path):
    """SHA256 hash of a file's contents."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _files_match(src, dst):
    """Check if installed file matches the bundled version."""
    if not os.path.exists(dst):
        return False
    try:
        return _file_hash(src) == _file_hash(dst)
    except Exception:
        return False


def _inputplumber_running():
    """Check if InputPlumber service is active."""
    try:
        r = subprocess.run(
            ["systemctl", "is-active", _SERVICE_NAME],
            capture_output=True, text=True, timeout=10, env=_clean_env()
        )
        return r.stdout.strip() == "active"
    except Exception:
        return False


def is_applied():
    """Check if the Apex InputPlumber profile is installed."""
    profile_installed = os.path.exists(_DEVICE_PROFILE_DST)
    capmap_installed = os.path.exists(_CAPABILITY_MAP_DST)

    profile_current = False
    capmap_current = False

    if profile_installed and os.path.exists(_DEVICE_PROFILE_SRC):
        profile_current = _files_match(_DEVICE_PROFILE_SRC, _DEVICE_PROFILE_DST)
    if capmap_installed and os.path.exists(_CAPABILITY_MAP_SRC):
        capmap_current = _files_match(_CAPABILITY_MAP_SRC, _CAPABILITY_MAP_DST)

    applied = profile_installed and capmap_installed
    ip_running = _inputplumber_running()

    return {
        "applied": applied,
        "profile_installed": profile_installed,
        "profile_current": profile_current,
        "capmap_installed": capmap_installed,
        "capmap_current": capmap_current,
        "inputplumber_running": ip_running,
    }


def check_compatibility():
    """Check if InputPlumber is available on the system."""
    ip_exists = os.path.exists("/usr/share/inputplumber/devices/")
    capmap_dir = os.path.exists("/usr/share/inputplumber/capability_maps/")

    if not ip_exists or not capmap_dir:
        return {
            "compatible": False,
            "message": "InputPlumber directories not found. Is InputPlumber installed?",
        }

    if not os.path.exists(_DEVICE_PROFILE_SRC):
        return {
            "compatible": False,
            "message": f"Bundled device profile not found at {_DEVICE_PROFILE_SRC}",
        }

    if not os.path.exists(_CAPABILITY_MAP_SRC):
        return {
            "compatible": False,
            "message": f"Bundled capability map not found at {_CAPABILITY_MAP_SRC}",
        }

    return {"compatible": True}


def _restart_inputplumber(steps):
    """Restart InputPlumber so it picks up the new profile."""
    _log_info("Restarting InputPlumber...")
    try:
        r = subprocess.run(
            ["systemctl", "restart", _SERVICE_NAME],
            capture_output=True, text=True, timeout=30, env=_clean_env()
        )
        if r.returncode == 0:
            steps.append("Restarted InputPlumber")
            _log_info("InputPlumber restarted")
            return True
        else:
            _log_warning(f"InputPlumber restart returned {r.returncode}: {r.stderr.strip()}")
            # Try enabling and starting if it wasn't running
            r2 = subprocess.run(
                ["systemctl", "enable", "--now", _SERVICE_NAME],
                capture_output=True, text=True, timeout=30, env=_clean_env()
            )
            if r2.returncode == 0:
                steps.append("Enabled and started InputPlumber")
                _log_info("InputPlumber enabled and started")
                return True
            _log_error(f"Failed to start InputPlumber: {r2.stderr.strip()}")
            steps.append("InputPlumber restart failed")
            return False
    except Exception as e:
        _log_error(f"InputPlumber restart exception: {e}")
        steps.append("InputPlumber restart failed")
        return False


def apply():
    """Install the Apex InputPlumber device profile and capability map."""
    steps = []
    _log_info("=== InputPlumber Profile Apply Start ===")

    status = is_applied()
    if status.get("applied") and status.get("profile_current") and status.get("capmap_current"):
        return {"success": True, "message": "Already applied", "steps": ["Already applied"]}

    # Check compatibility
    compat = check_compatibility()
    if not compat.get("compatible"):
        return {"success": False, "error": compat.get("message"), "steps": steps}

    # Install device profile
    try:
        shutil.copy2(_DEVICE_PROFILE_SRC, _DEVICE_PROFILE_DST)
        _log_info(f"Installed {_DEVICE_PROFILE_DST}")
        steps.append("Installed device profile")
    except Exception as e:
        _log_error(f"Failed to install device profile: {e}")
        return {"success": False, "error": f"Failed to install device profile: {e}", "steps": steps}

    # Install capability map
    try:
        shutil.copy2(_CAPABILITY_MAP_SRC, _CAPABILITY_MAP_DST)
        _log_info(f"Installed {_CAPABILITY_MAP_DST}")
        steps.append("Installed capability map")
    except Exception as e:
        _log_error(f"Failed to install capability map: {e}")
        # Rollback device profile
        try:
            os.remove(_DEVICE_PROFILE_DST)
        except Exception:
            pass
        return {"success": False, "error": f"Failed to install capability map: {e}", "steps": steps}

    # Restart InputPlumber to pick up new profile
    if not _restart_inputplumber(steps):
        return {"success": True, "warning": "Profile installed but InputPlumber restart may have failed", "steps": steps}

    _log_info("InputPlumber profile applied successfully")
    return {"success": True, "message": "InputPlumber profile installed and service restarted", "steps": steps}


def revert():
    """Remove the Apex InputPlumber profile and capability map."""
    steps = []
    _log_info("=== InputPlumber Profile Revert Start ===")

    # Remove device profile
    if os.path.exists(_DEVICE_PROFILE_DST):
        try:
            os.remove(_DEVICE_PROFILE_DST)
            steps.append("Removed device profile")
            _log_info(f"Removed {_DEVICE_PROFILE_DST}")
        except Exception as e:
            _log_warning(f"Failed to remove device profile: {e}")
    else:
        steps.append("Device profile not present")

    # Remove capability map
    if os.path.exists(_CAPABILITY_MAP_DST):
        try:
            os.remove(_CAPABILITY_MAP_DST)
            steps.append("Removed capability map")
            _log_info(f"Removed {_CAPABILITY_MAP_DST}")
        except Exception as e:
            _log_warning(f"Failed to remove capability map: {e}")
    else:
        steps.append("Capability map not present")

    # Restart InputPlumber so it drops the Apex composite device
    _restart_inputplumber(steps)

    _log_info("InputPlumber profile reverted")
    return {"success": True, "message": "InputPlumber profile removed", "steps": steps}


if __name__ == "__main__":
    import sys
    import json as _json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
    )

    usage = "Usage: sudo python3 button_fix.py [status|apply|revert|compat]"

    if len(sys.argv) < 2:
        print(usage)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        result = is_applied()
        print(_json.dumps(result, indent=2))
    elif cmd == "apply":
        result = apply()
        print(_json.dumps(result, indent=2))
        sys.exit(0 if result.get("success") else 1)
    elif cmd == "revert":
        result = revert()
        print(_json.dumps(result, indent=2))
        sys.exit(0 if result.get("success") else 1)
    elif cmd == "compat":
        result = check_compatibility()
        print(_json.dumps(result, indent=2))
    else:
        print(f"Unknown command: {cmd}")
        print(usage)
        sys.exit(1)
