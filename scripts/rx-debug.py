#!/usr/bin/env python3
"""Focused RX debug — logs every frame's raw RX value.

Run as root: sudo python3 rx-debug.py
Then slowly push right stick left, hold at full deflection, release.
"""
import glob
import os
import select
import struct
import time


def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])


def find_vendor_hidraw():
    for sysfs_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent_path = os.path.join(sysfs_path, "device", "uevent")
        if not os.path.exists(uevent_path):
            continue
        with open(uevent_path) as f:
            content = f.read()
        vid = pid = 0
        for line in content.splitlines():
            if line.startswith("HID_ID="):
                parts = line.split(":")
                if len(parts) >= 3:
                    vid = int(parts[1], 16)
                    pid = int(parts[2], 16)
        if vid != 0x1A86 or pid != 0xFE00:
            continue
        rd_path = os.path.join(sysfs_path, "device", "report_descriptor")
        if not os.path.exists(rd_path):
            continue
        with open(rd_path, "rb") as f:
            rd = f.read(3)
        if len(rd) >= 3 and rd[0] == 0x06 and rd[1] == 0x00 and rd[2] == 0xFF:
            name = os.path.basename(sysfs_path)
            return f"/dev/{name}"
    return None


VENDOR_DEV = find_vendor_hidraw()
if not VENDOR_DEV:
    print("ERROR: Could not find vendor hidraw device (1a86:fe00)")
    exit(1)

print(f"Vendor hidraw: {VENDOR_DEV}")
print("\nStopping HHD...")
os.system("systemctl stop hhd@$(logname) 2>/dev/null; systemctl stop hhd 2>/dev/null")
time.sleep(1)

vendor_fd = os.open(VENDOR_DEV, os.O_RDWR | os.O_NONBLOCK)
INTERCEPT_FULL = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
print(f"Sending FULL intercept...")
os.write(vendor_fd, INTERCEPT_FULL)
time.sleep(0.5)

# Drain
try:
    while True:
        os.read(vendor_fd, 64)
except BlockingIOError:
    pass

print("\n=== RX DEBUG ===")
print("Push right stick LEFT from center, hold, release.")
print("Every state packet will be logged with raw RX.")
print("Ctrl+C to quit\n")

prev_rx_raw = 0
frame = 0

# Simulate our fix logic to show what the output WOULD be
prev_rx_output = 0.0

try:
    while True:
        readable, _, _ = select.select([vendor_fd], [], [], 1.0)
        if vendor_fd in readable:
            try:
                while True:
                    try:
                        data = os.read(vendor_fd, 64)
                    except BlockingIOError:
                        break

                    if len(data) < 25 or data[0] != 0xB2 or data[3] != 0x02:
                        continue

                    frame += 1
                    rx_raw = struct.unpack_from("<h", data, 21)[0]
                    lx_raw = struct.unpack_from("<h", data, 17)[0]

                    # Our fix logic
                    raw_delta = abs(rx_raw - prev_rx_raw)
                    if raw_delta > 50000:
                        rx_fixed = -1.0 if prev_rx_raw < 0 else 1.0
                        flag = "WRAP"
                    else:
                        rx_fixed = max(-1.0, min(1.0, rx_raw / 32768.0))
                        prev_rx_raw = rx_raw
                        flag = ""

                    # Delta check (what the existing handler would do)
                    delta = abs(rx_fixed - prev_rx_output)
                    if delta > 1.5:
                        delta_flag = "DELTA_CLAMP"
                    elif delta < 0.002:
                        delta_flag = "skip"
                    else:
                        delta_flag = ""

                    if delta >= 0.002 or flag:  # only print when something changes
                        print(f"  [{frame:4d}] RX_raw={rx_raw:7d}  LX_raw={lx_raw:7d}  "
                              f"rx_fixed={rx_fixed:+7.4f}  prev_out={prev_rx_output:+7.4f}  "
                              f"raw_delta={raw_delta:6d}  out_delta={delta:.4f}  "
                              f"{flag:>5s} {delta_flag}")

                    if delta >= 0.002:
                        prev_rx_output = rx_fixed

            except BlockingIOError:
                pass

except KeyboardInterrupt:
    print(f"\n\nTotal frames: {frame}")
    print("Sending intercept OFF and restarting HHD...")
    INTERCEPT_OFF = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])
    os.write(vendor_fd, INTERCEPT_OFF)
    time.sleep(0.1)
    os.close(vendor_fd)
    os.system("systemctl restart hhd@$(logname) 2>/dev/null; systemctl restart hhd 2>/dev/null")
    print("Done.")
