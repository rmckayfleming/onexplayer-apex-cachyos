"""Back paddle support via firmware remap for OneXPlayer Apex.

The Apex has L4/R4 back paddles (M1/M2) connected through a vendor HID
device (VID:PID 1A86:FE00). By default, the firmware mirrors these as
B/Y on the Xbox gamepad (SECOND_FUNC mode, funcCode=0x05).

This module uses firmware-level button remapping (CID 0xB4) to assign
unique keyboard keycodes (F13/F14) to the paddles, then reads the
resulting B2 report-mode events and injects BTN_TRIGGER_HAPPY1/2 via
uinput so Steam Input recognizes them as L4/R4 back paddles.

No intercept mode needed — the Xbox gamepad stays fully active with
rumble/vibration support and native analog input.

Protocol:
  1. Send CID 0xB4 key mapping to remap M1→F14, M2→F13 (persists in firmware)
  2. Send B2 enable then B2 disable to activate "report mode" (flag 0x80)
  3. Read B2 report-mode packets for paddle press/release events
  4. Inject BTN_TRIGGER_HAPPY1/2 via uinput

B2 report-mode packet format (64 bytes):
  byte[0]  = 0xB2 (CID)
  byte[3]  = 0x01 (button event type)
  byte[5]  = 0x80 (report mode flag)
  byte[6]  = button code (0x22=M1, 0x23=M2)
  byte[7]  = funcCode (0x02=KEYBOARD when remapped)
  byte[12] = state (0x01=press, 0x02=release)
"""

import asyncio
import ctypes
import fcntl
import glob
import logging
import os
import struct
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

# Button states in B2 report-mode packets
STATE_PRESSED = 0x01
STATE_RELEASED = 0x02


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


# === Raw uinput ===

EV_SYN = 0x00
EV_KEY = 0x01
SYN_REPORT = 0x00
BTN_TRIGGER_HAPPY1 = 0x2C0
BTN_TRIGGER_HAPPY2 = 0x2C1
BUS_VIRTUAL = 0x06

UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_DEV_SETUP = 0x405C5503
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502

UINPUT_SETUP_FMT = "HHHh80sI"
INPUT_EVENT_FMT = "llHHi"


class RawUinputDevice:
    """Minimal uinput wrapper using raw ioctl — no python-evdev needed."""

    def __init__(self):
        self._fd = -1

    def create(self):
        self._fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self._fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self._fd, UI_SET_KEYBIT, BTN_TRIGGER_HAPPY1)
        fcntl.ioctl(self._fd, UI_SET_KEYBIT, BTN_TRIGGER_HAPPY2)

        name = b"OXP Apex Back Paddles"
        name_padded = name + b"\x00" * (80 - len(name))
        setup_data = struct.pack(
            UINPUT_SETUP_FMT,
            BUS_VIRTUAL, 0x1A86, 0xFE01, 1, name_padded, 0,
        )
        fcntl.ioctl(self._fd, UI_DEV_SETUP, setup_data)
        fcntl.ioctl(self._fd, UI_DEV_CREATE)
        time.sleep(0.1)

    def emit(self, ev_type, code, value):
        now = time.time()
        sec = int(now)
        usec = int((now - sec) * 1_000_000)
        event = struct.pack(INPUT_EVENT_FMT, sec, usec, ev_type, code, value)
        os.write(self._fd, event)

    def syn(self):
        self.emit(EV_SYN, SYN_REPORT, 0)

    def close(self):
        if self._fd >= 0:
            try:
                fcntl.ioctl(self._fd, UI_DEV_DESTROY)
            except OSError:
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1


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


def _check_paddle_remap(fd):
    """Check if paddles are already remapped by pressing test.

    Sends B2 cycle and waits briefly for any report-mode events to
    determine if the firmware remap is active. Returns True if we
    see KEYBOARD funcCode events for M1 or M2.

    Note: This is non-destructive — if no paddles are pressed during
    the check, we just assume remap is needed and apply it.
    """
    # We can't reliably query the B4 mapping (firmware ignores B4 reads).
    # Instead, we apply the remap unconditionally — it's idempotent.
    return False


# === Monitor ===

class BackPaddleMonitor:
    """Async monitor that reads firmware report-mode events and emits uinput."""

    def __init__(self):
        self._task = None
        self._running = False

    @property
    def is_running(self):
        return self._task is not None and not self._task.done()

    def get_status(self):
        """Return current status for the frontend."""
        return {
            "running": self.is_running,
            "mode": "firmware_remap",
        }

    async def _monitor_loop(self):
        """Main loop: remap → report mode → read events → uinput."""
        import select as _select
        self._running = True

        while self._running:
            dev_path = find_vendor_hidraw()
            if not dev_path:
                _log_warning("Vendor hidraw not found, retrying in 5s...")
                await asyncio.sleep(5)
                continue

            _log_info(f"Back paddle monitor opening {dev_path}")
            uinput_dev = None
            fd = -1
            try:
                fd = os.open(dev_path, os.O_RDWR | os.O_NONBLOCK)

                # Apply firmware remap (idempotent — persists in firmware)
                _apply_firmware_remap(fd)

                # Activate report mode
                if not _activate_report_mode(fd):
                    _log_error("Failed to activate report mode, retrying in 5s...")
                    os.close(fd)
                    fd = -1
                    await asyncio.sleep(5)
                    continue

                # Create uinput device
                uinput_dev = RawUinputDevice()
                uinput_dev.create()
                _log_info("Created uinput device: OXP Apex Back Paddles")

                # Read report-mode events
                while self._running:
                    try:
                        data = os.read(fd, 64)
                    except BlockingIOError:
                        await asyncio.sleep(0.005)
                        continue
                    except OSError:
                        _log_warning("Device read error, will reconnect...")
                        break

                    if not data or len(data) < 13:
                        await asyncio.sleep(0.005)
                        continue

                    # Filter: only B2 report-mode button events
                    if data[0] != 0xB2:
                        continue
                    if data[3] != 0x01:  # packet type = button event
                        continue
                    if data[5] != 0x80:  # report mode flag
                        continue

                    button_code = data[6]
                    state = data[12]

                    if button_code == BTN_M1:
                        evdev_btn = BTN_TRIGGER_HAPPY1
                        label = "M1(R)"
                    elif button_code == BTN_M2:
                        evdev_btn = BTN_TRIGGER_HAPPY2
                        label = "M2(L)"
                    else:
                        continue

                    if state == STATE_PRESSED:
                        uinput_dev.emit(EV_KEY, evdev_btn, 1)
                        uinput_dev.syn()
                    elif state == STATE_RELEASED:
                        uinput_dev.emit(EV_KEY, evdev_btn, 0)
                        uinput_dev.syn()

            except OSError as e:
                _log_error(f"Back paddle monitor error: {e}")
            finally:
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                if uinput_dev:
                    try:
                        uinput_dev.close()
                    except Exception:
                        pass

            if self._running:
                await asyncio.sleep(5)

    def start(self, loop):
        """Start the monitor as an async task."""
        if not self.is_running:
            self._running = True
            self._task = loop.create_task(self._monitor_loop())
            _log_info("Back paddle monitor starting (firmware remap mode)")

    async def stop(self):
        """Stop the monitor."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        _log_info("Back paddle monitor stopped")
