"""Back paddle setup via firmware remap for OneXPlayer Apex.

The Apex has L4/R4 back paddles (M1/M2) connected through a vendor HID
device (VID:PID 1A86:FE00). By default, the firmware mirrors these as
B/Y on the Xbox gamepad (SECOND_FUNC mode, funcCode=0x05).

This module uses firmware-level button remapping (CID 0xB4) to assign
unique keyboard keycodes (F13/F14) to the paddles, then activates
"report mode" so InputPlumber can read the paddle events via its
capability map (KeyF13→LeftPaddle1, KeyF14→RightPaddle1).
No separate uinput device needed.

No intercept mode — the Xbox gamepad stays fully active with
rumble/vibration support and native analog input.

Protocol:
  1. Send CID 0xB4 key mapping to remap M1→F14, M2→F13 (persists in firmware)
  2. Send B2 enable then B2 disable to activate "report mode" (flag 0x80)
  3. InputPlumber reads F13/F14 keyboard events and maps them to LeftPaddle1/RightPaddle1

B2 report-mode packet format (64 bytes):
  byte[0]  = 0xB2 (CID)
  byte[3]  = 0x01 (button event type)
  byte[5]  = 0x80 (report mode flag)
  byte[6]  = button code (0x22=M1, 0x23=M2)
  byte[7]  = funcCode (0x02=KEYBOARD when remapped)
  byte[12] = state (0x01=press, 0x02=release)
"""

import asyncio
import glob
import logging
import os
import time

logger = logging.getLogger("OXP-BackPaddle")

# Pluggable log callbacks — set by main.py to route logs to the plugin log file.
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


# === Device constants ===

TARGET_VID = 0x1A86
TARGET_PID = 0xFE00

# Button codes in B2 reports
BTN_M1 = 0x22  # Right back paddle
BTN_M2 = 0x23  # Left back paddle

# Function codes
FUNC_KEYBOARD = 0x02
FUNC_SECOND_FUNC = 0x05  # Default (duplicates Y/B)

# OXP key encoding: F(n) = 0x59 + n
OXP_KEY_F13 = 0x66
OXP_KEY_F14 = 0x67


# === HID v1 command framing ===

def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    """Generate an HID v1 command packet."""
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])


FUNC_XBOX = 0x01

B2_INTERCEPT_ON = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
B2_INTERCEPT_OFF = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])


def _build_b4_page2_remap(preset=0x01):
    """Build B4 page 2 packet: standard d-pad/buttons + M1→F14, M2→F13."""
    entries = [
        0x0A, FUNC_XBOX, 0x0A, 0x00, 0x00, 0x00,  # BACK
        0x0B, FUNC_XBOX, 0x0B, 0x00, 0x00, 0x00,  # L3
        0x0C, FUNC_XBOX, 0x0C, 0x00, 0x00, 0x00,  # R3
        0x0D, FUNC_XBOX, 0x0D, 0x00, 0x00, 0x00,  # UP
        0x0E, FUNC_XBOX, 0x0E, 0x00, 0x00, 0x00,  # DOWN
        0x0F, FUNC_XBOX, 0x0F, 0x00, 0x00, 0x00,  # LEFT
        0x10, FUNC_XBOX, 0x10, 0x00, 0x00, 0x00,  # RIGHT
        BTN_M1, FUNC_KEYBOARD, 0x01, OXP_KEY_F14, 0x00, 0x00,  # M1 (right) → F14
        BTN_M2, FUNC_KEYBOARD, 0x01, OXP_KEY_F13, 0x00, 0x00,  # M2 (left) → F13
    ]
    cmd = [0x02, 0x38, 0x20, 0x02, preset] + entries
    return gen_cmd_v1(0xB4, cmd)


def _build_b4_page1(preset=0x01):
    """Build B4 page 1 packet: standard face/shoulder buttons."""
    entries = [
        0x01, FUNC_XBOX, 0x01, 0x00, 0x00, 0x00,  # A
        0x02, FUNC_XBOX, 0x02, 0x00, 0x00, 0x00,  # B
        0x03, FUNC_XBOX, 0x03, 0x00, 0x00, 0x00,  # X
        0x04, FUNC_XBOX, 0x04, 0x00, 0x00, 0x00,  # Y
        0x05, FUNC_XBOX, 0x05, 0x00, 0x00, 0x00,  # LB
        0x06, FUNC_XBOX, 0x06, 0x00, 0x00, 0x00,  # RB
        0x07, FUNC_XBOX, 0x07, 0x00, 0x00, 0x00,  # LT
        0x08, FUNC_XBOX, 0x08, 0x00, 0x00, 0x00,  # RT
        0x09, FUNC_XBOX, 0x09, 0x00, 0x00, 0x00,  # START
    ]
    cmd = [0x02, 0x38, 0x20, 0x01, preset] + entries
    return gen_cmd_v1(0xB4, cmd)


# === Device discovery ===

def find_vendor_hidraw():
    """Find the vendor HID interface for the Apex (1A86:FE00, usage page 0xFF00)."""
    for sysfs_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent_path = os.path.join(sysfs_path, "device", "uevent")
        if not os.path.exists(uevent_path):
            continue
        with open(uevent_path) as f:
            content = f.read()
        for line in content.splitlines():
            if not line.startswith("HID_ID="):
                continue
            parts = line.split(":")
            if len(parts) < 3:
                continue
            vid = int(parts[1], 16)
            pid = int(parts[2], 16)
            if vid == TARGET_VID and pid == TARGET_PID:
                name = os.path.basename(sysfs_path)
                rd_path = os.path.join(sysfs_path, "device", "report_descriptor")
                if os.path.exists(rd_path):
                    try:
                        with open(rd_path, "rb") as f:
                            rd = f.read(3)
                        if len(rd) >= 3 and rd[0] == 0x06 and rd[1] == 0x00 and rd[2] == 0xFF:
                            dev_path = f"/dev/{name}"
                            if os.path.exists(dev_path):
                                return dev_path
                    except OSError:
                        pass
            break
    return None


# === Firmware remap helpers ===

def _apply_firmware_remap(fd):
    """Send B4 key mapping to remap M1→F14, M2→F13.

    Writes both pages (all buttons). The firmware accepts writes silently
    (no B4 response), but the remap persists across reboots.
    """
    _log_info("Applying firmware remap: M1→F14, M2→F13")
    try:
        os.write(fd, _build_b4_page1())
        time.sleep(0.1)
        os.write(fd, _build_b4_page2_remap())
        time.sleep(0.1)
        _log_info("Firmware remap commands sent")
        return True
    except OSError as e:
        _log_error(f"Firmware remap write failed: {e}")
        return False


def _activate_report_mode(fd):
    """Send B2 enable→disable cycle to activate report mode.

    After this cycle, the device spontaneously sends B2 packets with
    flag 0x80 for button presses, without entering intercept mode.
    """
    _log_info("Activating report mode (B2 cycle)...")
    try:
        os.write(fd, B2_INTERCEPT_ON)
        time.sleep(0.2)
        # Drain B2 ack responses
        import select
        for _ in range(20):
            ready, _, _ = select.select([fd], [], [], 0.05)
            if fd in ready:
                try:
                    os.read(fd, 256)
                except BlockingIOError:
                    break
            else:
                break
        os.write(fd, B2_INTERCEPT_OFF)
        time.sleep(0.1)
        # Drain disable ack
        for _ in range(10):
            ready, _, _ = select.select([fd], [], [], 0.05)
            if fd in ready:
                try:
                    os.read(fd, 256)
                except BlockingIOError:
                    break
            else:
                break
        _log_info("Report mode activated")
        return True
    except OSError as e:
        _log_error(f"Report mode activation failed: {e}")
        return False


# === One-shot setup ===

def setup_paddles():
    """Apply firmware remap and activate report mode (one-shot).

    After this, HHD reads paddle events natively via its virtual gamepad.
    Returns dict with success/error status.
    """
    dev_path = find_vendor_hidraw()
    if not dev_path:
        return {"success": False, "error": "Vendor hidraw not found"}

    _log_info(f"Setting up back paddles via {dev_path}")
    fd = -1
    try:
        fd = os.open(dev_path, os.O_RDWR | os.O_NONBLOCK)

        # Apply firmware remap (idempotent — persists in firmware flash)
        if not _apply_firmware_remap(fd):
            return {"success": False, "error": "Firmware remap failed"}

        # Activate report mode so HHD sees B2 events for M1/M2
        if not _activate_report_mode(fd):
            return {"success": False, "error": "Report mode activation failed"}

        _log_info("Back paddle setup complete — InputPlumber will handle events")
        return {"success": True}
    except OSError as e:
        _log_error(f"Back paddle setup error: {e}")
        return {"success": False, "error": str(e)}
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


class BackPaddleMonitor:
    """Compatibility wrapper — runs one-shot setup, no event loop needed.

    HHD handles paddle events natively via extra_l1/extra_r1 on its
    virtual gamepad. This class just applies firmware remap + report mode.
    """

    def __init__(self):
        self._task = None
        self._setup_done = False

    @property
    def is_running(self):
        return self._setup_done

    def get_status(self):
        return {
            "running": self._setup_done,
            "mode": "firmware_remap",
        }

    async def _do_setup(self):
        """Run setup with retries until vendor device is found."""
        for attempt in range(6):
            result = await asyncio.to_thread(setup_paddles)
            if result.get("success"):
                self._setup_done = True
                return
            _log_warning(f"Paddle setup attempt {attempt + 1} failed: {result.get('error')}, retrying in 5s...")
            await asyncio.sleep(5)
        _log_error("Back paddle setup failed after retries")

    def start(self, loop):
        if not self._setup_done:
            self._task = loop.create_task(self._do_setup())
            _log_info("Back paddle setup starting (firmware remap + report mode)")

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        _log_info("Back paddle setup stopped")
