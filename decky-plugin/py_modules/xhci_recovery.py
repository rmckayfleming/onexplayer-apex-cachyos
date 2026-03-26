"""xHCI boot recovery for OneXPlayer Apex.

The internal gamepad (1A86:FE00 + 045E:028E) connects via xHCI controller
PCI 0000:65:00.4. This controller sometimes dies during boot with:

    xhci_hcd 0000:65:00.4: xHCI host not responding to stop endpoint command
    xhci_hcd 0000:65:00.4: HC died; cleaning up

When this happens, the gamepad disappears and HHD can't create a virtual
controller. This module detects a dead xHCI and rebinds it on plugin startup.
"""

import logging
import os
import subprocess
import time

logger = logging.getLogger("OXP-xHCIRecovery")

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


_PCI_DEVICE = "0000:65:00.4"
_XHCI_DRIVER = "xhci_hcd"
_UNBIND_PATH = f"/sys/bus/pci/drivers/{_XHCI_DRIVER}/unbind"
_BIND_PATH = f"/sys/bus/pci/drivers/{_XHCI_DRIVER}/bind"
_DRIVER_LINK = f"/sys/bus/pci/devices/{_PCI_DEVICE}/driver"

# USB devices that should be present when xHCI is healthy
_VENDOR_HID_ID = "1a86:fe00"
_XBOX_GAMEPAD_ID = "045e:028e"


def _clean_env():
    env = os.environ.copy()
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        env.pop(var, None)
    return env


def _usb_device_present(vid_pid):
    """Check if a USB device with the given VID:PID is enumerated."""
    try:
        r = subprocess.run(
            ["lsusb", "-d", vid_pid],
            capture_output=True, text=True, timeout=5, env=_clean_env()
        )
        return r.returncode == 0 and vid_pid in r.stdout
    except Exception:
        return False


def _xhci_bound():
    """Check if the xHCI PCI device is bound to its driver."""
    return os.path.exists(_DRIVER_LINK)


def _rebind_xhci():
    """Unbind and rebind the xHCI controller to recover USB devices."""
    _log_info(f"Rebinding xHCI controller {_PCI_DEVICE}...")

    # Unbind if currently bound (may be in a dead state)
    if _xhci_bound():
        try:
            with open(_UNBIND_PATH, "w") as f:
                f.write(_PCI_DEVICE)
            _log_info("Unbound xHCI controller")
        except OSError as e:
            _log_warning(f"Unbind failed (may already be unbound): {e}")

    time.sleep(1)

    # Bind
    try:
        with open(_BIND_PATH, "w") as f:
            f.write(_PCI_DEVICE)
        _log_info("Bound xHCI controller")
    except OSError as e:
        _log_error(f"Bind failed: {e}")
        return False

    # Wait for USB enumeration
    time.sleep(2)
    return True


def check_and_recover():
    """Check if the internal gamepad is present; rebind xHCI if not.

    Returns a dict with recovery status:
      - needed: whether recovery was attempted
      - success: whether the gamepad was found after recovery
      - already_ok: True if no recovery was needed
    """
    # Check if gamepad USB devices are already present
    if _usb_device_present(_VENDOR_HID_ID) and _usb_device_present(_XBOX_GAMEPAD_ID):
        _log_info("Internal gamepad USB devices present — no recovery needed")
        return {"needed": False, "success": True, "already_ok": True}

    _log_warning("Internal gamepad USB devices missing — attempting xHCI recovery")

    if not os.path.exists(f"/sys/bus/pci/devices/{_PCI_DEVICE}"):
        _log_error(f"PCI device {_PCI_DEVICE} not found — cannot recover")
        return {"needed": True, "success": False, "already_ok": False}

    # Try up to 2 rebind attempts
    for attempt in range(1, 3):
        _log_info(f"xHCI recovery attempt {attempt}/2")
        _rebind_xhci()

        if _usb_device_present(_VENDOR_HID_ID) and _usb_device_present(_XBOX_GAMEPAD_ID):
            _log_info(f"xHCI recovery succeeded on attempt {attempt}")
            return {"needed": True, "success": True, "already_ok": False}

        if attempt < 2:
            _log_warning("Gamepad not yet detected, retrying...")
            time.sleep(2)

    _log_error("xHCI recovery failed — gamepad not detected after 2 attempts")
    return {"needed": True, "success": False, "already_ok": False}
