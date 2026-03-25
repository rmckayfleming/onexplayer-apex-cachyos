#!/usr/bin/env python3
"""Test if vibration persists through intercept re-enable.

Run as root with HHD stopped:
    sudo systemctl stop hhd@$(whoami)
    sudo python3 scripts/test-rumble-persist.py
    sudo systemctl start hhd@$(whoami)

Sequence:
  1. Enable intercept
  2. Send 0xB3 vibration (intercept exits, motors start)
  3. Immediately re-enable intercept
  4. Wait — does vibration CONTINUE through re-enable?
  5. Send 0xB3 stop vibration
  6. Immediately re-enable intercept again
"""

import glob
import os
import select
import sys
import time


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
            return f"/dev/{os.path.basename(sysfs_path)}"
    return None


def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])


def gen_intercept_enable():
    return gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])


def gen_intercept_disable():
    return gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])


def gen_vibration_start(strength=255):
    cmd = (
        [0x02, 0x38, 0x02, 0xE3, 0x39, 0xE3, 0x39, 0xE3, 0x39,
         0x01, strength, strength, 0xE3, 0x39, 0xE3]
        + [0x00] * 35
        + [0x39, 0xE3, 0x39, 0xE3, 0xE3, 0x02, 0x04, 0x39, 0x39]
    )
    return gen_cmd_v1(0xB3, cmd)


def gen_vibration_stop():
    cmd = (
        [0x02, 0x38, 0x02, 0xE3, 0x39, 0xE3, 0x39, 0xE3, 0x39,
         0x02, 0x00, 0x00, 0xE3, 0x39, 0xE3]
        + [0x00] * 35
        + [0x39, 0xE3, 0x39, 0xE3, 0xE3, 0x02, 0x04, 0x39, 0x39]
    )
    return gen_cmd_v1(0xB3, cmd)


def check_intercept_alive(fd, timeout=1.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        r, _, _ = select.select([fd], [], [], min(remaining, 0.1))
        if fd in r:
            data = os.read(fd, 64)
            if len(data) >= 1 and data[0] == 0xB2:
                return True
    return False


def drain(fd, duration=0.3):
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        r, _, _ = select.select([fd], [], [], 0.05)
        if fd in r:
            os.read(fd, 64)


def main():
    if os.geteuid() != 0:
        print("ERROR: Run as root")
        sys.exit(1)

    vendor_path = find_vendor_hidraw()
    if not vendor_path:
        print("ERROR: Vendor HID not found")
        sys.exit(1)
    print(f"Vendor HID: {vendor_path}")

    fd = os.open(vendor_path, os.O_RDWR)

    # Step 1: Enable intercept
    print("\n[1] Enabling intercept mode...")
    os.write(fd, gen_intercept_enable())
    time.sleep(1)
    alive = check_intercept_alive(fd)
    print(f"    Intercept active: {alive}")
    if not alive:
        print("    ABORT: intercept didn't activate")
        os.close(fd)
        sys.exit(1)
    drain(fd)

    # Step 2: Send vibration (this will exit intercept)
    print("\n[2] Sending 0xB3 vibration START (strength=255)...")
    os.write(fd, gen_vibration_start(255))

    # Step 3: Immediately re-enable intercept (no delay!)
    print("[3] Immediately re-enabling intercept...")
    os.write(fd, gen_intercept_enable())
    time.sleep(0.2)

    alive = check_intercept_alive(fd)
    print(f"    Intercept active: {alive}")
    print("    >>> IS THE CONTROLLER STILL VIBRATING? <<<")
    print("    (waiting 3 seconds...)")
    time.sleep(3)

    # Step 4: Send vibration stop
    print("\n[4] Sending 0xB3 vibration STOP...")
    os.write(fd, gen_vibration_stop())

    # Step 5: Re-enable intercept again
    print("[5] Re-enabling intercept...")
    os.write(fd, gen_intercept_enable())
    time.sleep(0.2)

    alive = check_intercept_alive(fd)
    print(f"    Intercept active: {alive}")
    print("    >>> DID VIBRATION STOP? <<<")
    time.sleep(1)

    # Step 6: Test input during intercept — move the sticks
    print("\n[6] Intercept input test — move your sticks for 3 seconds...")
    pkt_count = 0
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        r, _, _ = select.select([fd], [], [], 0.1)
        if fd in r:
            data = os.read(fd, 64)
            if len(data) >= 4 and data[0] == 0xB2 and data[3] == 0x02:
                pkt_count += 1
    print(f"    Received {pkt_count} analog state packets")
    if pkt_count > 10:
        print("    Intercept input is WORKING")
    else:
        print("    WARNING: Low packet count — sticks may not be working")

    # Cleanup
    print("\n[7] Disabling intercept...")
    os.write(fd, gen_intercept_disable())
    time.sleep(0.3)
    os.close(fd)

    print("\n=== SUMMARY ===")
    print("Key question: Did vibration PERSIST through step 3 (intercept re-enable)?")
    print("If YES → we can cycle 0xB3 + 0xB2 with minimal disruption")
    print("If NO  → vibration and intercept are mutually exclusive in firmware")


if __name__ == "__main__":
    main()
