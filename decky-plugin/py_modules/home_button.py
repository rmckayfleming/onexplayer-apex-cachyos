"""Home button monitor for OneXPlayer Apex.

Watches the Apex keyboard HID device (1a86:fe00) via /dev/hidraw* and
launches hhd-ui when the Home/Orange button is pressed.
"""

import asyncio
import glob
import logging
import os
import subprocess
import time

logger = logging.getLogger("OXP-HomeButton")

TARGET_VID = 0x1A86
TARGET_PID = 0xFE00

# Home button: LCtrl + LAlt + LGUI (modifier byte 0x0D, no key codes)
MOD_HOME_BUTTON = 0x0D

DEFAULT_CMD = "xdg-open http://localhost:5335"
DEBOUNCE_SECS = 2.0


def find_hidraw_device():
    """Find the hidraw device node for the Apex keyboard (1a86:fe00)."""
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
                dev_path = f"/dev/{name}"
                if os.path.exists(dev_path):
                    return dev_path
    return None


class HomeButtonMonitor:
    """Async monitor for the home button via hidraw."""

    def __init__(self, cmd=DEFAULT_CMD):
        self.cmd = cmd
        self._task = None
        self._running = False

    @property
    def is_running(self):
        return self._task is not None and not self._task.done()

    async def _monitor_loop(self):
        """Main monitoring loop — runs until cancelled."""
        self._running = True
        last_trigger = 0.0

        while self._running:
            dev_path = find_hidraw_device()
            if not dev_path:
                logger.warning("hidraw device not found, retrying in 5s...")
                await asyncio.sleep(5)
                continue

            logger.info(f"Monitoring {dev_path} for Home button")
            try:
                fd = os.open(dev_path, os.O_RDONLY | os.O_NONBLOCK)
                try:
                    while self._running:
                        try:
                            data = os.read(fd, 8)
                        except BlockingIOError:
                            await asyncio.sleep(0.05)
                            continue
                        except OSError:
                            break

                        if not data or len(data) < 8:
                            await asyncio.sleep(0.05)
                            continue

                        modifier = data[0]
                        if modifier == MOD_HOME_BUTTON:
                            now = time.monotonic()
                            if now - last_trigger < DEBOUNCE_SECS:
                                continue
                            last_trigger = now
                            logger.info("Home button pressed — launching HHD UI")
                            try:
                                subprocess.Popen(
                                    self.cmd, shell=True,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    env={
                                        **os.environ,
                                        "DISPLAY": ":0",
                                        "WAYLAND_DISPLAY": "wayland-0",
                                        "XDG_RUNTIME_DIR": "/run/user/1000",
                                    },
                                )
                            except Exception as e:
                                logger.error(f"Failed to launch: {e}")
                finally:
                    os.close(fd)
            except OSError as e:
                logger.error(f"Error opening {dev_path}: {e}")
                await asyncio.sleep(5)

    def start(self, loop):
        """Start the monitor as an async task."""
        if not self.is_running:
            self._running = True
            self._task = loop.create_task(self._monitor_loop())

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
