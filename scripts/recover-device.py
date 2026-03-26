#!/usr/bin/env python3
"""Try to recover the vendor HID device from a bad state.

Sends B2 commands to cycle intercept on/off and check if device responds.
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
        if os.path.exists(rd_path):
            with open(rd_path, "rb") as f:
                rd = f.read(3)
            if len(rd) >= 3 and rd[0] == 0x06 and rd[1] == 0x00 and rd[2] == 0xFF:
                return f"/dev/{os.path.basename(sysfs_path)}"
    return None


def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])


def hid_read(fd, timeout_ms=500):
    ready, _, _ = select.select([fd], [], [], timeout_ms / 1000.0)
    if fd in ready:
        try:
            return os.read(fd, 256)
        except:
            pass
    return None


def hex_dump(data):
    return " ".join(f"{b:02x}" for b in data)


def main():
    dev = find_vendor_hidraw()
    if not dev:
        print("ERROR: Vendor hidraw not found")
        sys.exit(1)

    print(f"Device: {dev}")
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)

    # Step 1: Check if device responds to anything
    print("\n1) Sending B2 intercept enable...")
    cmd = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
    os.write(fd, cmd)
    time.sleep(0.3)
    for _ in range(10):
        resp = hid_read(fd, 300)
        if resp:
            print(f"   << ({len(resp)}b): {hex_dump(resp[:20])}")
        else:
            break

    # Step 2: Disable intercept
    print("\n2) Sending B2 intercept disable...")
    cmd = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])
    os.write(fd, cmd)
    time.sleep(0.3)
    for _ in range(10):
        resp = hid_read(fd, 300)
        if resp:
            print(f"   << ({len(resp)}b): {hex_dump(resp[:20])}")
        else:
            break

    # Step 3: Try factory reset CID (0xB4 with reset payload)
    print("\n3) Trying B4 factory-style resets...")
    for label, cmd in [
        ("B4 [0xFF]", gen_cmd_v1(0xB4, [0xFF])),
        ("B4 [0x00]", gen_cmd_v1(0xB4, [0x00])),
        ("B4 [0x00,0x00]", gen_cmd_v1(0xB4, [0x00, 0x00])),
    ]:
        os.write(fd, cmd)
        time.sleep(0.2)
        resp = hid_read(fd, 200)
        if resp:
            print(f"   {label}: << {hex_dump(resp[:20])}")
        else:
            print(f"   {label}: (no response)")

    # Step 4: Drain and listen for 5s
    print("\n4) Listening for 5 seconds — press paddles now...")
    deadline = time.monotonic() + 5.0
    got_any = False
    while time.monotonic() < deadline:
        resp = hid_read(fd, 200)
        if resp:
            print(f"   << ({len(resp)}b): {hex_dump(resp[:20])}")
            got_any = True

    if not got_any:
        print("   (no events)")

    os.close(fd)
    print("\nDone. If no events, a reboot may be needed to reset the firmware.")


if __name__ == "__main__":
    main()
