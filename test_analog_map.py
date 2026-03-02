#!/usr/bin/env python3
"""Map the GAMEPAD_STATE packet format for analog axes.

Shows bytes 6-25 for each axis position.
Run as root: sudo python3 test_analog_map.py
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

fd = os.open(VENDOR_DEV, os.O_RDWR)
os.write(fd, gen_cmd_v1(0xB2, [0x03, 0x01, 0x02]))
time.sleep(0.2)

# Drain
os.set_blocking(fd, False)
try:
    while True:
        os.read(fd, 64)
except BlockingIOError:
    pass

def read_gamepad_state():
    """Read latest GAMEPAD_STATE, draining any queued packets."""
    os.set_blocking(fd, False)
    last = None
    try:
        while True:
            data = os.read(fd, 64)
            if data[0] == 0xB2 and data[3] == 0x02:
                last = data
    except BlockingIOError:
        pass
    return last

AXES = [
    ("HOLD left stick fully LEFT", "LX min"),
    ("HOLD left stick fully RIGHT", "LX max"),
    ("HOLD left stick fully UP", "LY min"),
    ("HOLD left stick fully DOWN", "LY max"),
    ("HOLD right stick fully LEFT", "RX min"),
    ("HOLD right stick fully RIGHT", "RX max"),
    ("HOLD right stick fully UP", "RY min"),
    ("HOLD right stick fully DOWN", "RY max"),
    ("PULL left trigger FULLY", "LT max"),
    ("PULL right trigger FULLY", "RT max"),
]

print("\nFor each prompt: HOLD the position, then press Enter.\n")

results = {}
for prompt, label in AXES:
    input(f"{prompt} -> Enter: ")
    time.sleep(0.05)
    sample = read_gamepad_state()
    if sample:
        data = sample[6:26]
        # Show each byte with position
        byte_str = " ".join(f"{b:02x}" for b in data)
        print(f"  {label}: [{byte_str}]")
        # Show non-zero bytes
        nonzero = [(i+6, b) for i, b in enumerate(data) if b != 0]
        if nonzero:
            print(f"  Non-zero: {', '.join(f'[{pos}]=0x{val:02x}' for pos, val in nonzero)}")
        results[label] = data
    else:
        print(f"  No data received - try holding the input longer before pressing Enter")

print("\n=== SUMMARY ===")
for label, data in results.items():
    nonzero = [(i+6, b) for i, b in enumerate(data) if b != 0]
    nz_str = ", ".join(f"[{pos}]=0x{val:02x}" for pos, val in nonzero)
    print(f"  {label:8s}: {nz_str}")

print("\nSending intercept OFF and restarting HHD...")
os.set_blocking(fd, True)
os.write(fd, gen_cmd_v1(0xB2, [0x00, 0x01, 0x02]))
time.sleep(0.1)
os.close(fd)
os.system("systemctl restart hhd")
print("Done.")
