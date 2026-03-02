#!/usr/bin/env python3
"""Dump ALL packets from vendor device during intercept — no filtering.

Looking for analog stick/trigger data in non-button packet types.
Run as root with HHD stopped: sudo python3 test_vendor_all.py
"""
import os
import select
import time

VENDOR_DEV = "/dev/hidraw5"

def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])

print("Stopping HHD...")
os.system("systemctl stop hhd")
time.sleep(1)

fd = os.open(VENDOR_DEV, os.O_RDWR | os.O_NONBLOCK)
INTERCEPT_ON = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
os.write(fd, INTERCEPT_ON)
time.sleep(0.2)

# Drain initial responses
try:
    while True:
        d = os.read(fd, 64)
        print(f"  init: {d.hex()}")
except BlockingIOError:
    pass

print(f"\nDumping ALL vendor packets. Move sticks, pull triggers, press buttons.")
print(f"Ctrl+C to quit\n")

os.set_blocking(fd, True)
seen_types = set()

try:
    while True:
        data = os.read(fd, 64)
        if not data:
            continue

        cid = data[0]
        pkt_type = data[3] if len(data) > 3 else -1

        key = (cid, pkt_type)
        is_new = key not in seen_types

        # Always show button events and new packet types
        if pkt_type == 0x01 or is_new:
            seen_types.add(key)
            label = ""
            if pkt_type == 0x01:
                btn = data[6] if len(data) > 6 else 0
                state = data[12] if len(data) > 12 else 0
                names = {0x22: "L4", 0x23: "R4"}
                label = f"  btn={names.get(btn, f'0x{btn:02x}')} {'PRESS' if state==1 else 'RELEASE'}"
            if is_new:
                label += " [NEW TYPE]"
            print(f"cid=0x{cid:02x} type=0x{pkt_type:02x}{label}")
            print(f"  {data.hex()}")
        elif pkt_type == 0x02:
            # Gamepad state — show full hex to look for analog values
            print(f"cid=0x{cid:02x} type=0x02 GAMEPAD_STATE")
            print(f"  {data.hex()}")

except KeyboardInterrupt:
    print("\n\nSending intercept OFF and restarting HHD...")
    INTERCEPT_OFF = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])
    try:
        os.set_blocking(fd, False)
        os.write(fd, INTERCEPT_OFF)
    except:
        pass
    time.sleep(0.1)
    os.close(fd)
    os.system("systemctl restart hhd")
    print("Done.")
