#!/usr/bin/env python3
"""Test if Xbox gamepad still sends analog data while intercept is active.

Stops HHD, sends intercept on vendor device, then monitors the REAL Xbox gamepad.
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

print("Stopping HHD so we can read the raw Xbox gamepad...")
os.system("systemctl stop hhd")
time.sleep(1)

# Open vendor device and send intercept
vendor_fd = os.open(VENDOR_DEV, os.O_RDWR | os.O_NONBLOCK)
INTERCEPT_ON = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
os.write(vendor_fd, INTERCEPT_ON)
print(f"Intercept ON sent to {VENDOR_DEV}")
time.sleep(0.2)

# Drain ACK
try:
    while True:
        os.read(vendor_fd, 64)
except BlockingIOError:
    pass

# Open real Xbox gamepad
xbox_fd = os.open(XBOX_DEV, os.O_RDONLY | os.O_NONBLOCK)
INPUT_EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(INPUT_EVENT_FMT)

print(f"\nInterceept ON. Now move LEFT STICK, pull TRIGGERS, press A button.")
print(f"Watching real Xbox gamepad ({XBOX_DEV}) + vendor device...")
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
                    if ev_type == 1:  # EV_KEY
                        print(f"  [XBOX]    BUTTON code={code} (0x{code:04x}) value={value}")
                    elif ev_type == 3:  # EV_ABS
                        ABS_NAMES = {0: "LX", 1: "LY", 2: "RX", 3: "RY", 4: "?4", 5: "RZ(RT)",
                                     16: "DPAD_X", 17: "DPAD_Y", 10: "Z(LT)"}
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
    print("Done. HHD restarted.")
