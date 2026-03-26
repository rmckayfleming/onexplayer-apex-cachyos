"""Back paddle uinput daemon for OneXPlayer Apex.

Reads B2 report-mode packets from the vendor hidraw device and injects
F13/F14 keyboard events via uinput so InputPlumber can map them to
LeftPaddle1/RightPaddle1.

The vendor HID device sends B2 packets (CID 0xB2, flag 0x80) for paddle
presses, but the kernel HID driver doesn't translate these into standard
keyboard events. This daemon bridges the gap.

B2 packet format (64 bytes):
  byte[0]  = 0xB2 (CID)
  byte[5]  = 0x80 (report mode flag)
  byte[6]  = button code (0x22=M1/right, 0x23=M2/left)
  byte[12] = state (0x01=press, 0x02=release)
"""

import asyncio
import ctypes
import ctypes.util
import glob
import logging
import os
import struct
import time

logger = logging.getLogger("OXP-PaddleDaemon")

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


# ---- Constants ----

TARGET_VID = 0x1A86
TARGET_PID = 0xFE00

# B2 packet fields
CID_B2 = 0xB2
FLAG_REPORT_MODE = 0x80
BTN_M1 = 0x22  # Right paddle
BTN_M2 = 0x23  # Left paddle
STATE_PRESS = 0x01
STATE_RELEASE = 0x02

# Linux input event codes
EV_SYN = 0x00
EV_KEY = 0x01
SYN_REPORT = 0x00
KEY_F13 = 183
KEY_F14 = 184

# uinput constants
UI_DEV_SETUP = 0x405C5503
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565

# Button code → key code mapping
PADDLE_MAP = {
    BTN_M1: KEY_F14,  # Right paddle → F14
    BTN_M2: KEY_F13,  # Left paddle → F13
}


# ---- uinput helpers ----

class UinputDevice:
    """Minimal uinput wrapper to inject keyboard events."""

    def __init__(self):
        self.fd = -1

    def create(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)

        # Set EV_KEY capability
        _ioctl_int(self.fd, UI_SET_EVBIT, EV_KEY)

        # Set key capabilities for F13 and F14
        _ioctl_int(self.fd, UI_SET_KEYBIT, KEY_F13)
        _ioctl_int(self.fd, UI_SET_KEYBIT, KEY_F14)

        # Setup device info
        # struct uinput_setup { struct input_id id; char name[80]; u32 ff_effects_max; }
        # struct input_id { u16 bustype, vendor, product, version }
        name = b"OXP Apex Back Paddles"
        name_padded = name + b"\x00" * (80 - len(name))
        setup_data = struct.pack("<HHHH80sI",
            0x06,   # BUS_VIRTUAL
            0x1A86, # vendor
            0xFE01, # product (distinct from the real device)
            0x0001, # version
            name_padded,
            0,      # ff_effects_max
        )
        _ioctl_bytes(self.fd, UI_DEV_SETUP, setup_data)
        _ioctl_none(self.fd, UI_DEV_CREATE)
        _log_info("uinput device created: OXP Apex Back Paddles")

    def inject_key(self, code, value):
        """Inject a key press (value=1) or release (value=0) event."""
        now = time.time()
        sec = int(now)
        usec = int((now - sec) * 1_000_000)
        # Write EV_KEY event
        ev = struct.pack("llHHi", sec, usec, EV_KEY, code, value)
        os.write(self.fd, ev)
        # Write SYN_REPORT
        syn = struct.pack("llHHi", sec, usec, EV_SYN, SYN_REPORT, 0)
        os.write(self.fd, syn)

    def destroy(self):
        if self.fd >= 0:
            try:
                _ioctl_none(self.fd, UI_DEV_DESTROY)
            except OSError:
                pass
            os.close(self.fd)
            self.fd = -1
            _log_info("uinput device destroyed")


def _ioctl_int(fd, request, value):
    import fcntl
    fcntl.ioctl(fd, request, value)


def _ioctl_bytes(fd, request, data):
    import fcntl
    fcntl.ioctl(fd, request, data)


def _ioctl_none(fd, request):
    import fcntl
    fcntl.ioctl(fd, request)


# ---- hidraw discovery ----

def find_vendor_hidraw():
    """Find the vendor HID interface (1a86:fe00, usage page 0xFF00)."""
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


# ---- Daemon ----

class PaddleDaemon:
    """Async daemon that reads B2 hidraw packets and injects uinput key events."""

    def __init__(self):
        self._task = None
        self._running = False
        self._uinput = None

    @property
    def is_running(self):
        return self._task is not None and not self._task.done()

    def get_status(self):
        return {
            "running": self.is_running,
            "mode": "uinput_bridge",
        }

    async def _daemon_loop(self):
        self._running = True

        while self._running:
            # Find hidraw device
            dev_path = find_vendor_hidraw()
            if not dev_path:
                _log_warning("Vendor hidraw not found, retrying in 5s...")
                await asyncio.sleep(5)
                continue

            _log_info(f"Paddle daemon monitoring {dev_path}")

            # Create uinput device if needed
            if self._uinput is None:
                try:
                    self._uinput = UinputDevice()
                    self._uinput.create()
                    # Give uinput a moment to register
                    await asyncio.sleep(0.5)
                except OSError as e:
                    _log_error(f"Failed to create uinput device: {e}")
                    await asyncio.sleep(5)
                    continue

            fd = -1
            try:
                fd = os.open(dev_path, os.O_RDONLY | os.O_NONBLOCK)
                while self._running:
                    try:
                        data = os.read(fd, 64)
                    except BlockingIOError:
                        await asyncio.sleep(0.01)
                        continue
                    except OSError:
                        break

                    if not data or len(data) < 13:
                        await asyncio.sleep(0.01)
                        continue

                    # Check for B2 report-mode packet
                    if data[0] != CID_B2:
                        continue
                    if len(data) > 5 and data[5] != FLAG_REPORT_MODE:
                        continue

                    btn = data[6]
                    state = data[12]

                    key_code = PADDLE_MAP.get(btn)
                    if key_code is None:
                        continue

                    if state == STATE_PRESS:
                        self._uinput.inject_key(key_code, 1)
                        btn_name = "M1/R" if btn == BTN_M1 else "M2/L"
                        key_name = "F14" if key_code == KEY_F14 else "F13"
                        _log_info(f"Paddle {btn_name} press → {key_name}")
                    elif state == STATE_RELEASE:
                        self._uinput.inject_key(key_code, 0)

            except OSError as e:
                _log_error(f"Error reading {dev_path}: {e}")
                await asyncio.sleep(5)
            finally:
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    def start(self, loop):
        if not self.is_running:
            self._running = True
            self._task = loop.create_task(self._daemon_loop())
            _log_info("Paddle daemon starting (uinput bridge)")

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._uinput:
            self._uinput.destroy()
            self._uinput = None
        _log_info("Paddle daemon stopped")
