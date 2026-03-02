#!/usr/bin/env python3
"""Quick CLI test: send intercept command to hidraw5, read back paddle events.

Run as root: sudo python3 test_paddles.py

Press L4/R4 back paddles and see if we get separate 0x22/0x23 events
instead of B/Y mirroring.
"""
import os
import sys
import time

DEV = "/dev/hidraw5"

def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    """Generate an HID v1 command packet."""
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])

INTERCEPT_ON  = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
INTERCEPT_OFF = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])

print(f"Opening {DEV}...")
fd = os.open(DEV, os.O_RDWR)

print(f"Sending v1 intercept ON command ({len(INTERCEPT_ON)} bytes):")
print(f"  {INTERCEPT_ON.hex()}")
os.write(fd, INTERCEPT_ON)
print("Sent! Waiting 0.1s for ACK...")
time.sleep(0.1)

# Try to read any ACK/response
try:
    os.set_blocking(fd, False)
    while True:
        try:
            data = os.read(fd, 64)
            print(f"  Response: {data.hex()}")
            print(f"  byte[0]={hex(data[0])} byte[1]={hex(data[1])} byte[3]={hex(data[3])}")
        except BlockingIOError:
            break
except Exception as e:
    print(f"  Read error: {e}")

os.set_blocking(fd, True)

print("\nNow press L4/R4 back paddles (Ctrl+C to quit)...")
print("Looking for: byte[0]=0xB2, byte[3]=0x01, byte[6]=button, byte[12]=state\n")

try:
    while True:
        data = os.read(fd, 64)
        if not data:
            continue

        # Show full hex
        full_hex = data.hex()

        if data[0] == 0xB2 and len(data) >= 13:
            pkt_type = data[3]
            btn = data[6]
            state = data[12]
            # HHD validation check
            valid = data[1] == 0x3F and data[-2] == 0x3F

            type_name = {0x01: "BUTTON", 0x02: "GAMEPAD", 0x03: "ACK"}.get(pkt_type, f"0x{pkt_type:02x}")

            if pkt_type == 0x01:
                btn_name = {0x22: "L4", 0x23: "R4"}.get(btn, f"0x{btn:02x}")
                state_name = {0x01: "PRESS", 0x02: "RELEASE"}.get(state, f"0x{state:02x}")
                print(f"** {type_name}: {btn_name} {state_name} ** valid={valid}  byte[-2]=0x{data[-2]:02x} byte[-1]=0x{data[-1]:02x}")
                print(f"   full: {full_hex}")
            else:
                print(f"   {type_name}: btn=0x{btn:02x} state=0x{state:02x} valid={valid}  full: {full_hex}")
        else:
            print(f"   other[{len(data)}]: {full_hex}")

except KeyboardInterrupt:
    print("\n\nSending intercept OFF...")
    os.write(fd, INTERCEPT_OFF)
    time.sleep(0.1)
    os.close(fd)
    print("Done.")
