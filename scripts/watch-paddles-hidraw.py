#!/usr/bin/env python3
"""Listen-only paddle monitor on vendor HID. No commands sent.

Run as root with HHD stopped:
    sudo systemctl stop hhd
    sudo python3 scripts/watch-paddles-hidraw.py
"""

import glob
import os
import select
import sys
import time


# OXP key encoding: F(n) = 0x59 + n
OXP_KEYS = {}
for n in range(1, 25):
    OXP_KEYS[0x59 + n] = f"F{n}"
# Add common keys
OXP_KEYS.update({0x4B: "PageUp", 0x4E: "PageDown"})

BUTTONS = {
    0x21: "HOME", 0x22: "M1(R-paddle)", 0x23: "M2(L-paddle)",
    0x24: "KB/QAM", 0x25: "M4", 0x26: "M5", 0x27: "M6",
    0x01: "A", 0x02: "B", 0x03: "X", 0x04: "Y",
    0x05: "LB", 0x06: "RB", 0x09: "Start", 0x0A: "Back",
}

FUNCS = {0x01: "XBOX", 0x02: "KEYBOARD", 0x03: "MACRO", 0x04: "TURBO", 0x05: "SECOND_FUNC"}


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


def main():
    if os.geteuid() != 0:
        print("ERROR: Run as root")
        sys.exit(1)

    dev = find_vendor_hidraw()
    if not dev:
        print("ERROR: Vendor hidraw not found")
        sys.exit(1)

    print(f"Listening on {dev} (read-only, no commands sent)")
    print("Press paddles. Ctrl+C to stop.\n")

    fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 1.0)
            if fd in ready:
                try:
                    data = os.read(fd, 256)
                except BlockingIOError:
                    continue

                if len(data) < 8 or data[0] != 0xB2:
                    print(f"  [other] {' '.join(f'{b:02x}' for b in data[:20])}")
                    continue

                pkt_type = data[3]
                if pkt_type == 0x01 and len(data) >= 13:
                    btn_code = data[6]
                    func_code = data[7]
                    v1 = data[8]
                    v2 = data[9]
                    state = data[12]

                    btn_name = BUTTONS.get(btn_code, f"0x{btn_code:02x}")
                    func_name = FUNCS.get(func_code, f"0x{func_code:02x}")
                    state_name = "PRESS" if state == 1 else "RELEASE" if state == 2 else f"state={state}"

                    if func_code == 0x02:  # KEYBOARD
                        key_name = OXP_KEYS.get(v2, f"0x{v2:02x}")
                        print(f"  {btn_name:16s} {state_name:8s}  -> {func_name} {key_name} (v1=0x{v1:02x} v2=0x{v2:02x})")
                    else:
                        print(f"  {btn_name:16s} {state_name:8s}  -> {func_name} (v1=0x{v1:02x} v2=0x{v2:02x})")
                elif pkt_type == 0x02:
                    pass  # analog state, skip
                else:
                    print(f"  [type=0x{pkt_type:02x}] {' '.join(f'{b:02x}' for b in data[:20])}")

    except KeyboardInterrupt:
        print("\nDone.")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
