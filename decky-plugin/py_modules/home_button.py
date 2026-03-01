"""Home button monitor for OneXPlayer Apex.

The Apex has a Home/Orange button that sends a keyboard combo (LCtrl+LAlt+LGUI)
via a secondary USB HID keyboard device (VID:PID 1a86:fe00). This is separate
from the main controller — HHD doesn't see it by default.

This module watches the raw HID reports from that keyboard device via
/dev/hidraw* and launches hhd-ui (the Handheld Daemon web UI) when the
Home button is pressed.

HID report format (8 bytes):
  Byte 0: Modifier keys bitmask (LCtrl=0x01, LShift=0x02, LAlt=0x04, LGUI=0x08, ...)
  Byte 1: Reserved
  Bytes 2-7: Up to 6 simultaneous key codes

The Home button sends modifier byte 0x0D (LCtrl + LAlt + LGUI) with no key codes.
"""

import asyncio
import glob
import logging
import os
import time

logger = logging.getLogger("OXP-HomeButton")

# Pluggable log callbacks — set by main.py to route logs to the plugin log file.
_log_info_cb = None
_log_error_cb = None
_log_warning_cb = None


def set_log_callbacks(info_fn, error_fn, warning_fn):
    """Set external log callbacks (called by main.py to wire into plugin logging)."""
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


# USB VID:PID for the Apex's secondary keyboard HID device
TARGET_VID = 0x1A86
TARGET_PID = 0xFE00

# Modifier bitmask for the Home button press: LCtrl(0x01) + LAlt(0x04) + LGUI(0x08) = 0x0D
MOD_HOME_BUTTON = 0x0D

# Minimum time between triggers to prevent double-firing from key bounce
DEBOUNCE_SECS = 2.0


def _toggle_hhd_overlay():
    """Toggle HHD overlay via its REST API.

    Reads the auth token from /tmp/hhd/token and sends a POST to the
    local HHD API. This works as root since it's a localhost HTTP call,
    no display server access needed.
    """
    import json
    import urllib.request
    import urllib.error

    token_path = "/tmp/hhd/token"
    try:
        with open(token_path) as f:
            token = f.read().strip()
    except FileNotFoundError:
        _log_error(f"HHD token not found at {token_path}")
        return

    # Use the state endpoint to toggle the overlay
    url = "http://localhost:5335/api/v1/state"
    payload = json.dumps({"shortcuts": {"open_hhd": True}}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        _log_info(f"HHD overlay toggle: HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        _log_error(f"HHD API error {e.code}: {body}")
    except Exception as e:
        _log_error(f"HHD API request failed: {e}")


def find_hidraw_device():
    """Find the hidraw device node for the Apex keyboard (1a86:fe00).

    Walks /sys/class/hidraw/hidraw* and checks each device's uevent file
    for a matching VID:PID. Returns the /dev/hidrawN path if found.
    """
    for sysfs_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent_path = os.path.join(sysfs_path, "device", "uevent")
        if not os.path.exists(uevent_path):
            continue
        with open(uevent_path) as f:
            content = f.read()
        for line in content.splitlines():
            if not line.startswith("HID_ID="):
                continue
            # HID_ID format: "0003:00001A86:0000FE00" (bus:vid:pid)
            parts = line.split(":")
            if len(parts) < 3:
                continue
            vid = int(parts[1], 16)
            pid = int(parts[2], 16)
            if vid == TARGET_VID and pid == TARGET_PID:
                name = os.path.basename(sysfs_path)
                dev_path = f"/dev/{name}"
                if os.path.exists(dev_path):
                    return dev_path
    return None


class HomeButtonMonitor:
    """Async monitor that watches the Home button via hidraw and toggles HHD overlay."""

    def __init__(self):
        self._task = None
        self._running = False

    @property
    def is_running(self):
        return self._task is not None and not self._task.done()

    async def _monitor_loop(self):
        """Main monitoring loop — opens the hidraw device and reads HID reports.

        If the device disappears (e.g. USB reset), it retries every 5 seconds.
        """
        self._running = True
        last_trigger = 0.0

        while self._running:
            # Find the hidraw device — it may not exist yet on boot
            dev_path = find_hidraw_device()
            if not dev_path:
                _log_warning("hidraw device not found, retrying in 5s...")
                await asyncio.sleep(5)
                continue

            _log_info(f"Monitoring {dev_path} for Home button")
            try:
                # Open in non-blocking mode so we can check _running between reads
                fd = os.open(dev_path, os.O_RDONLY | os.O_NONBLOCK)
                try:
                    while self._running:
                        try:
                            # Read one HID report (8 bytes for a standard keyboard)
                            data = os.read(fd, 8)
                        except BlockingIOError:
                            # No data available — sleep briefly and retry
                            await asyncio.sleep(0.05)
                            continue
                        except OSError:
                            # Device disconnected — break out to retry
                            break

                        if not data or len(data) < 8:
                            await asyncio.sleep(0.05)
                            continue

                        # Check if the modifier byte matches the Home button combo
                        modifier = data[0]
                        if modifier == MOD_HOME_BUTTON:
                            now = time.monotonic()
                            # Debounce: ignore rapid repeated presses
                            if now - last_trigger < DEBOUNCE_SECS:
                                continue
                            last_trigger = now
                            _log_info("Home button pressed — toggling HHD overlay")
                            try:
                                _toggle_hhd_overlay()
                            except Exception as e:
                                _log_error(f"Failed to toggle overlay: {e}")
                finally:
                    os.close(fd)
            except OSError as e:
                _log_error(f"Error opening {dev_path}: {e}")
                await asyncio.sleep(5)

    def start(self, loop):
        """Start the monitor as an async task on the given event loop."""
        if not self.is_running:
            self._running = True
            self._task = loop.create_task(self._monitor_loop())

    async def stop(self):
        """Stop the monitor and clean up the async task."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
