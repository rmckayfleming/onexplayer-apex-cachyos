#!/usr/bin/env python3
"""
home-button-hhd.py — Launch HHD UI when Home/Orange button is pressed

Monitors the OneXFly Apex keyboard HID device (1a86:fe00) via /dev/hidraw*
to detect the Home button press and launch HHD's web UI.

Why hidraw? HHD grabs the evdev device exclusively, but hidraw is a separate
subsystem — multiple processes can read from the same hidraw device.

Home button sends: LCtrl + LAlt + LGUI (modifier-only, no key codes)
In HID terms: modifier byte 0x0D (bits 0, 2, 3)

Usage:
    sudo python3 home-button-hhd.py              # monitor and launch HHD UI
    sudo python3 home-button-hhd.py --debug       # print raw HID reports
    sudo python3 home-button-hhd.py --cmd 'hhd-ui'  # custom launch command
"""

import argparse
import glob
import os
import subprocess
import sys
import time

# USB VID:PID for the OneXFly Apex keyboard HID device
TARGET_VID = 0x1A86
TARGET_PID = 0xFE00

# HID keyboard modifier bits for the Home/Orange button
# Actual combo: LCtrl (bit 0) + LAlt (bit 2) + LGUI (bit 3) = 0x0D
MOD_HOME_BUTTON = 0x0D

# Default command to run
DEFAULT_CMD = "xdg-open http://localhost:5335"

# Debounce: ignore repeated triggers within this window (seconds)
DEBOUNCE_SECS = 2.0


def find_hidraw_device():
    """Find the hidraw device node for our target VID:PID."""
    for sysfs_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent_path = os.path.join(sysfs_path, "device", "uevent")
        if not os.path.exists(uevent_path):
            continue
        with open(uevent_path) as f:
            content = f.read()
        # HID_ID line format: HID_ID=0003:00001A86:0000FE00
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


def debug_mode(dev_path):
    """Print raw HID reports for debugging (skips idle/zero reports)."""
    print(f"DEBUG: Reading raw HID reports from {dev_path}")
    print("Press buttons to see their HID data (idle reports suppressed). Ctrl+C to stop.\n")
    with open(dev_path, "rb") as f:
        while True:
            data = f.read(8)
            if not data:
                continue
            # Skip idle/zero reports to avoid spam
            if all(b == 0 for b in data):
                continue
            hex_str = " ".join(f"{b:02x}" for b in data)
            modifier = data[0]
            keys = list(data[2:8])
            mod_names = []
            if modifier & (1 << 0):
                mod_names.append("LCtrl")
            if modifier & (1 << 1):
                mod_names.append("LShift")
            if modifier & (1 << 2):
                mod_names.append("LAlt")
            if modifier & (1 << 3):
                mod_names.append("LGUI")
            if modifier & (1 << 4):
                mod_names.append("RCtrl")
            if modifier & (1 << 5):
                mod_names.append("RShift")
            if modifier & (1 << 6):
                mod_names.append("RAlt")
            if modifier & (1 << 7):
                mod_names.append("RGUI")
            active_keys = [k for k in keys if k != 0]
            print(
                f"[{hex_str}]  mod={'+'.join(mod_names) or 'none'}  "
                f"keys={active_keys or 'none'}"
            )


def monitor(dev_path, cmd):
    """Monitor for Home button and launch command."""
    print(f"Monitoring {dev_path} for Home button (LCtrl+LAlt+LGUI)...")
    print(f"Will run: {cmd}")

    last_trigger = 0.0

    with open(dev_path, "rb") as f:
        while True:
            data = f.read(8)
            if not data or len(data) < 8:
                continue

            modifier = data[0]

            # Home button: LCtrl+LAlt+LGUI (modifier byte 0x0D, no keys)
            if modifier == MOD_HOME_BUTTON:
                now = time.monotonic()
                if now - last_trigger < DEBOUNCE_SECS:
                    continue
                last_trigger = now
                print(f"[{time.strftime('%H:%M:%S')}] Home button pressed — launching HHD UI")
                try:
                    subprocess.Popen(
                        cmd,
                        shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception as e:
                    print(f"Failed to launch: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Launch HHD UI on Home button press")
    parser.add_argument(
        "--debug", action="store_true", help="Print raw HID reports for debugging"
    )
    parser.add_argument(
        "--cmd",
        default=DEFAULT_CMD,
        help=f"Command to run on button press (default: {DEFAULT_CMD})",
    )
    args = parser.parse_args()

    dev_path = find_hidraw_device()
    if not dev_path:
        print(
            f"Could not find hidraw device for {TARGET_VID:04x}:{TARGET_PID:04x}",
            file=sys.stderr,
        )
        print("Make sure the device is connected and try: ls /dev/hidraw*", file=sys.stderr)
        sys.exit(1)

    print(f"Found device: {dev_path}")

    if args.debug:
        debug_mode(dev_path)
    else:
        monitor(dev_path, args.cmd)


if __name__ == "__main__":
    main()
