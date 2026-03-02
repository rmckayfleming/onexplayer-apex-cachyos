#!/usr/bin/env python3
"""Test if Xbox gamepad still works while intercept is active on vendor device.

This opens hidraw5 separately (like back_paddle.py would), sends intercept,
and checks if the Xbox gamepad evdev still sends face button events.

Run as root: sudo python3 test_intercept_coexist.py
"""
import os
import select
import struct
import time

VENDOR_DEV = "/dev/hidraw5"

GAMEPAD_DEV = "/dev/input/event24"

print(f"Vendor device: {VENDOR_DEV}")
print(f"Gamepad evdev: {GAMEPAD_DEV}")

def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])

# Open vendor device for intercept
vendor_fd = os.open(VENDOR_DEV, os.O_RDWR | os.O_NONBLOCK)
print(f"\nSending intercept ON to {VENDOR_DEV}...")
INTERCEPT_ON = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
os.write(vendor_fd, INTERCEPT_ON)
time.sleep(0.2)

# Drain ACK
try:
    while True:
        os.read(vendor_fd, 64)
except BlockingIOError:
    pass

# Open gamepad evdev
gamepad_fd = os.open(GAMEPAD_DEV, os.O_RDONLY | os.O_NONBLOCK)
INPUT_EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(INPUT_EVENT_FMT)

print(f"\nInterceept ON. Now press A, B, X, Y and L4/R4.")
print(f"Watching both vendor device AND gamepad evdev...")
print(f"Ctrl+C to quit\n")

try:
    while True:
        readable, _, _ = select.select([vendor_fd, gamepad_fd], [], [], 1.0)

        if vendor_fd in readable:
            try:
                data = os.read(vendor_fd, 64)
                if data[0] == 0xB2 and len(data) >= 13 and data[3] == 0x01:
                    btn = data[6]
                    state = data[12]
                    btn_names = {0x22: "L4", 0x23: "R4"}
                    btn_name = btn_names.get(btn, f"btn_0x{btn:02x}")
                    state_name = "PRESS" if state == 1 else "RELEASE"
                    print(f"  [VENDOR]  {btn_name} {state_name}")
            except BlockingIOError:
                pass

        if gamepad_fd in readable:
            try:
                while True:
                    raw = os.read(gamepad_fd, EVENT_SIZE)
                    if len(raw) < EVENT_SIZE:
                        break
                    sec, usec, ev_type, code, value = struct.unpack(INPUT_EVENT_FMT, raw)
                    if ev_type == 0:  # SYN
                        continue
                    if ev_type == 1:  # EV_KEY
                        print(f"  [GAMEPAD] key code={code} (0x{code:04x}) value={value}")
                    elif ev_type == 3:  # EV_ABS
                        print(f"  [GAMEPAD] axis code={code} (0x{code:04x}) value={value}")
            except BlockingIOError:
                pass

except KeyboardInterrupt:
    print("\n\nSending intercept OFF...")
    INTERCEPT_OFF = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])
    os.write(vendor_fd, INTERCEPT_OFF)
    time.sleep(0.1)
    os.close(vendor_fd)
    os.close(gamepad_fd)
    print("Done.")
