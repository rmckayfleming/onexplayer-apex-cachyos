#!/usr/bin/env python3
"""Test partial intercept — try intercepting only page 2 of button table.

Theory: [0x03, 0x01, 0x02] = intercept pages 1-2 (all buttons)
        [0x03, 0x02, 0x02] = intercept page 2 only (back paddles + some)

Run as root with HHD stopped: sudo python3 test_partial_intercept.py
"""
import os
import select
import struct
import time

VENDOR_DEV = "/dev/hidraw5"
XBOX_DEV = "/dev/input/event15"  # Real Xbox 360 pad

def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])

print("Stopping HHD...")
os.system("systemctl stop hhd")
time.sleep(1)

vendor_fd = os.open(VENDOR_DEV, os.O_RDWR | os.O_NONBLOCK)
xbox_fd = os.open(XBOX_DEV, os.O_RDONLY | os.O_NONBLOCK)

# Try partial intercept: only page 2
INTERCEPT_PAGE2 = gen_cmd_v1(0xB2, [0x03, 0x02, 0x02])
print(f"Sending PARTIAL intercept (page 2 only): {INTERCEPT_PAGE2[:8].hex()}...")
os.write(vendor_fd, INTERCEPT_PAGE2)
time.sleep(0.2)

# Drain ACK
try:
    while True:
        data = os.read(vendor_fd, 64)
        print(f"  ACK: {data[:16].hex()}...")
except BlockingIOError:
    pass

INPUT_EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(INPUT_EVENT_FMT)

ABS_NAMES = {0: "LX", 1: "LY", 2: "RX", 3: "RY", 5: "RZ(RT)", 10: "Z(LT)", 16: "DPAD_X", 17: "DPAD_Y"}

print(f"\nPartial intercept ON. Press A, B, move stick, press L4/R4.")
print(f"Ctrl+C to quit\n")

try:
    while True:
        readable, _, _ = select.select([vendor_fd, xbox_fd], [], [], 1.0)

        if vendor_fd in readable:
            try:
                data = os.read(vendor_fd, 64)
                if data[0] == 0xB2 and len(data) >= 13 and data[3] == 0x01:
                    btn = data[6]
                    state = data[12]
                    names = {0x22: "L4", 0x23: "R4"}
                    name = names.get(btn, f"btn_0x{btn:02x}")
                    st = "PRESS" if state == 1 else "RELEASE"
                    print(f"  [VENDOR]  {name} {st}")
            except BlockingIOError:
                pass

        if xbox_fd in readable:
            try:
                while True:
                    raw = os.read(xbox_fd, EVENT_SIZE)
                    if len(raw) < EVENT_SIZE:
                        break
                    sec, usec, ev_type, code, value = struct.unpack(INPUT_EVENT_FMT, raw)
                    if ev_type == 0:
                        continue
                    if ev_type == 1:
                        print(f"  [XBOX]    BUTTON code={code} (0x{code:04x}) value={value}")
                    elif ev_type == 3:
                        name = ABS_NAMES.get(code, f"abs_{code}")
                        print(f"  [XBOX]    AXIS {name} = {value}")
            except BlockingIOError:
                pass

except KeyboardInterrupt:
    print("\n\nSending intercept OFF and restarting HHD...")
    INTERCEPT_OFF = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])
    os.write(vendor_fd, INTERCEPT_OFF)
    time.sleep(0.1)
    os.close(vendor_fd)
    os.close(xbox_fd)
    os.system("systemctl restart hhd")
    print("Done.")
