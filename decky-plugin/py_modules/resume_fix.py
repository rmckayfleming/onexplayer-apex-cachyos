"""Resume recovery fix for OneXPlayer Apex.

Installs a dbus-monitor daemon that listens for sleep/resume events and
rebinds the xHCI USB controller to recover the gamepad after sleep.

Based on the xpad-fix3 package from the Korean OXP community.
PCI device 0000:65:00.4 is the xHCI controller connected to the gamepad.
"""

import logging
import os
import subprocess

logger = logging.getLogger("OXP-ResumeFix")

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


_SCRIPT_PATH = "/usr/local/sbin/apex-resume-recover.sh"
_SERVICE_NAME = "apex-resume-recover.service"
_SERVICE_PATH = f"/etc/systemd/system/{_SERVICE_NAME}"
_PCI_DEVICE = "0000:65:00.4"
_PCI_DRIVER_PATH = f"/sys/bus/pci/devices/{_PCI_DEVICE}/driver"

_SCRIPT_CONTENT = f"""#!/bin/bash
# OneXPlayer Apex — Gamepad recovery after sleep
# Listens for resume events via dbus and rebinds the xHCI controller.
# Based on xpad-fix3 from the Korean OXP community.

XHCI_PCI="{_PCI_DEVICE}"
XHCI_DRIVER="/sys/bus/pci/devices/$XHCI_PCI/driver"

recover_gamepad() {{
    logger -t apex-resume-recover "Resume detected — recovering gamepad"

    # Phase 1: quick recovery (1s delay)
    sleep 1
    if [ -e "$XHCI_DRIVER" ]; then
        echo "$XHCI_PCI" > "$XHCI_DRIVER/unbind" 2>/dev/null
        sleep 0.5
    fi
    echo "$XHCI_PCI" > /sys/bus/pci/drivers/xhci_hcd/bind 2>/dev/null

    # Phase 2: fallback if phase 1 didn't work (2s delay)
    sleep 2
    if [ ! -e "$XHCI_DRIVER" ]; then
        logger -t apex-resume-recover "Phase 1 failed, retrying bind"
        echo "$XHCI_PCI" > /sys/bus/pci/drivers/xhci_hcd/bind 2>/dev/null
    fi

    logger -t apex-resume-recover "Recovery complete"
}}

# Monitor dbus for resume events
dbus-monitor --system "type='signal',interface='org.freedesktop.login1.Manager',member='PrepareForSleep'" | \\
while read -r line; do
    if echo "$line" | grep -q "boolean false"; then
        recover_gamepad &
    fi
done
"""

_SERVICE_CONTENT = f"""[Unit]
Description=OneXPlayer Apex gamepad resume recovery
After=dbus.service
Wants=dbus.service

[Service]
Type=simple
ExecStart={_SCRIPT_PATH}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def _pci_device_exists():
    """Check if the target PCI device exists."""
    return os.path.exists(f"/sys/bus/pci/devices/{_PCI_DEVICE}")


def is_applied():
    """Check current status of the resume recovery fix."""
    service_exists = os.path.exists(_SERVICE_PATH)
    script_exists = os.path.exists(_SCRIPT_PATH)
    pci_exists = _pci_device_exists()

    service_active = False
    service_enabled = False
    if service_exists:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", _SERVICE_NAME],
                capture_output=True, text=True, timeout=10, env=_clean_env()
            )
            service_active = r.stdout.strip() == "active"
        except Exception:
            pass
        try:
            r = subprocess.run(
                ["systemctl", "is-enabled", _SERVICE_NAME],
                capture_output=True, text=True, timeout=10, env=_clean_env()
            )
            service_enabled = r.stdout.strip() == "enabled"
        except Exception:
            pass

    applied = service_active and service_enabled and script_exists

    return {
        "applied": applied,
        "service_active": service_active,
        "service_enabled": service_enabled,
        "script_exists": script_exists,
        "pci_device_exists": pci_exists,
    }


def apply():
    """Install and start the resume recovery service."""
    steps = []
    _log_info("=== Resume Fix Apply Start ===")

    status = is_applied()
    if status.get("applied"):
        return {"success": True, "message": "Already applied", "steps": ["Already applied"]}

    if not _pci_device_exists():
        _log_warning(f"PCI device {_PCI_DEVICE} not found — service may not work")
        steps.append(f"Warning: PCI device {_PCI_DEVICE} not found")

    # Write recovery script
    try:
        os.makedirs(os.path.dirname(_SCRIPT_PATH), exist_ok=True)
        with open(_SCRIPT_PATH, "w") as f:
            f.write(_SCRIPT_CONTENT)
        os.chmod(_SCRIPT_PATH, 0o755)
        steps.append("Created recovery script")
        _log_info(f"Created {_SCRIPT_PATH}")
    except Exception as e:
        return {"success": False, "error": f"Failed to write script: {e}", "steps": steps}

    # Write systemd service
    try:
        with open(_SERVICE_PATH, "w") as f:
            f.write(_SERVICE_CONTENT)
        steps.append("Created systemd service")
        _log_info(f"Created {_SERVICE_PATH}")
    except Exception as e:
        return {"success": False, "error": f"Failed to write service: {e}", "steps": steps}

    # Enable and start
    try:
        subprocess.run(
            ["systemctl", "daemon-reload"],
            capture_output=True, text=True, timeout=30, env=_clean_env()
        )
        r = subprocess.run(
            ["systemctl", "enable", "--now", _SERVICE_NAME],
            capture_output=True, text=True, timeout=30, env=_clean_env()
        )
        if r.returncode == 0:
            steps.append("Enabled and started service")
            _log_info("Resume fix service started")
        else:
            _log_error(f"systemctl enable --now failed: {r.stderr.strip()}")
            return {"success": False, "error": f"Failed to start service: {r.stderr.strip()}", "steps": steps}
    except Exception as e:
        return {"success": False, "error": f"systemctl failed: {e}", "steps": steps}

    _log_info("Resume fix applied successfully")
    return {"success": True, "message": "Resume recovery service installed", "steps": steps}


def revert():
    """Stop and remove the resume recovery service."""
    steps = []
    _log_info("=== Resume Fix Revert Start ===")

    # Disable and stop service
    if os.path.exists(_SERVICE_PATH):
        try:
            subprocess.run(
                ["systemctl", "disable", "--now", _SERVICE_NAME],
                capture_output=True, text=True, timeout=30, env=_clean_env()
            )
            steps.append("Disabled resume recovery service")
        except Exception as e:
            _log_warning(f"Failed to disable service: {e}")

    # Remove service file
    if os.path.exists(_SERVICE_PATH):
        try:
            os.remove(_SERVICE_PATH)
            steps.append("Removed service file")
        except Exception as e:
            _log_warning(f"Failed to remove service file: {e}")

    # Remove script
    if os.path.exists(_SCRIPT_PATH):
        try:
            os.remove(_SCRIPT_PATH)
            steps.append("Removed recovery script")
        except Exception as e:
            _log_warning(f"Failed to remove script: {e}")

    # Reload systemd
    try:
        subprocess.run(
            ["systemctl", "daemon-reload"],
            capture_output=True, text=True, timeout=30, env=_clean_env()
        )
    except Exception:
        pass

    _log_info("Resume fix reverted")
    return {"success": True, "message": "Resume recovery service removed", "steps": steps}
