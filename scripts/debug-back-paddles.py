#!/usr/bin/env python3
"""Debug back paddle setup and monitor for events.

Usage: sudo python3 scripts/debug-back-paddles.py

Step 1: Finds vendor hidraw device and sends firmware remap + B2 activation
Step 2: Monitors ALL input devices for any events from paddle presses
"""

import glob
import os
import select
import signal
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "decky-plugin", "py_modules"))
import back_paddle

# ---- Step 0: Show hidraw devices ----

print("=== Step 0: HID devices ===")
for sysfs_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
    name = os.path.basename(sysfs_path)
    uevent_path = os.path.join(sysfs_path, "device", "uevent")
    if os.path.exists(uevent_path):
        with open(uevent_path) as f:
            content = f.read()
        for line in content.splitlines():
            if line.startswith("HID_ID="):
                print(f"  /dev/{name}: {line}")
                break

print()

# ---- Step 1: Find vendor hidraw ----

print("=== Step 1: Finding vendor hidraw ===")
dev = back_paddle.find_vendor_hidraw()
if dev:
    print(f"  Found: {dev}")
else:
    print("  NOT FOUND!")
    print("  Looking for 1a86:fe00 with usage page 0xFF00")
    print()
    # Show all hidraw report descriptors for 1a86:fe00
    for sysfs_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent_path = os.path.join(sysfs_path, "device", "uevent")
        if not os.path.exists(uevent_path):
            continue
        with open(uevent_path) as f:
            content = f.read()
        if "1A86" in content.upper() and "FE00" in content.upper():
            name = os.path.basename(sysfs_path)
            rd_path = os.path.join(sysfs_path, "device", "report_descriptor")
            if os.path.exists(rd_path):
                with open(rd_path, "rb") as f:
                    rd = f.read(10)
                print(f"  /dev/{name}: report_descriptor first 10 bytes = {rd.hex()}")
            else:
                print(f"  /dev/{name}: no report_descriptor")
    sys.exit(1)

print()

# ---- Step 2: Send firmware remap + B2 activation ----

print("=== Step 2: Firmware remap + B2 activation ===")

back_paddle.set_log_callbacks(
    lambda msg: print(f"  [INFO]  {msg}"),
    lambda msg: print(f"  [ERROR] {msg}"),
    lambda msg: print(f"  [WARN]  {msg}"),
)

result = back_paddle.setup_paddles()
print()
if result.get("success"):
    print("  Setup reported SUCCESS")
else:
    print(f"  Setup reported FAILURE: {result.get('error')}")

print()

# ---- Step 3: Also try reading raw hidraw for B2 packets ----

print("=== Step 3: Monitoring for paddle events ===")
print("  Press both back paddles now! Ctrl+C to stop.")
print("  Monitoring evdev (event2,5,6,7,8) AND hidraw...")
print()

signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

TYPE_NAMES = {1: "KEY", 2: "REL", 3: "ABS", 4: "MSC"}
KEY_NAMES = {
    183: "F13", 184: "F14", 29: "LCTRL", 56: "LALT", 125: "LGUI",
    32: "D", 34: "G",
}

fds = {}
poll = select.poll()

# Open evdev devices
for ev in ["event2", "event5", "event6", "event7", "event8"]:
    path = f"/dev/input/{ev}"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        fds[fd] = ("evdev", path)
        poll.register(fd, select.POLLIN)
    except OSError:
        pass

# Open all hidraw devices for 1a86:fe00
for sysfs_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
    uevent_path = os.path.join(sysfs_path, "device", "uevent")
    if not os.path.exists(uevent_path):
        continue
    with open(uevent_path) as f:
        content = f.read()
    if "1A86" in content.upper() and "FE00" in content.upper():
        name = os.path.basename(sysfs_path)
        path = f"/dev/{name}"
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            fds[fd] = ("hidraw", path)
            poll.register(fd, select.POLLIN)
            print(f"  Opened {path} (hidraw)")
        except OSError as e:
            print(f"  SKIP {path}: {e}")

print()

try:
    while True:
        for fd, mask in poll.poll(200):
            kind, path = fds[fd]
            try:
                data = os.read(fd, 256)
            except OSError:
                continue

            if kind == "evdev" and len(data) >= 24:
                sec, usec, typ, code, val = struct.unpack("llHHi", data[:24])
                if typ != 0:
                    type_name = TYPE_NAMES.get(typ, str(typ))
                    key_name = KEY_NAMES.get(code, str(code))
                    state = {0: "UP", 1: "DOWN", 2: "REPEAT"}.get(val, str(val))
                    print(f"  [evdev {path:20s}]  {type_name:>4} code={code:<4} ({key_name:<8}) {state}")
            elif kind == "hidraw" and len(data) > 0:
                # Skip empty/zero reports (hidraw2 spams these)
                if all(b == 0 for b in data):
                    continue
                # Show first 16 bytes hex
                hex_str = data[:16].hex(" ")
                cid = data[0] if len(data) > 0 else 0
                extra = ""
                if cid == 0xB2 and len(data) >= 13:
                    btn = data[6] if len(data) > 6 else 0
                    state = data[12] if len(data) > 12 else 0
                    flag = data[5] if len(data) > 5 else 0
                    btn_name = {0x22: "M1", 0x23: "M2"}.get(btn, f"0x{btn:02x}")
                    state_name = {1: "press", 2: "release"}.get(state, f"0x{state:02x}")
                    extra = f"  → B2 {btn_name} {state_name} (flag=0x{flag:02x})"
                print(f"  [hidraw {path:19s}]  CID=0x{cid:02X}  {hex_str}{extra}")
except KeyboardInterrupt:
    pass
