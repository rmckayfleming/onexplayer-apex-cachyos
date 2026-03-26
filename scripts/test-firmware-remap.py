#!/usr/bin/env python3
"""Test firmware-level button remapping via CID 0xB4.

Remaps M1/M2 back paddles to unique keyboard keys (F13/F14) at the firmware
level, eliminating the need for intercept mode and preserving rumble support.

Run as root with HHD stopped:
    sudo systemctl stop hhd
    sudo python3 test-firmware-remap.py

Protocol discovered by sniffing OneXConsole <-> device HID traffic on Windows.
The device responds on interface MI_02 (usage page 0xFF00, vendor-specific).
"""
import glob
import os
import select
import struct
import sys
import time


# === Key code table (OneXConsole encoding) ===
# Pattern: F(n) = 0x59 + n, so F1=0x5A, F12=0x65, F13=0x66, etc.
KEY_CODES = {
    "F1": 0x5A, "F2": 0x5B, "F3": 0x5C, "F4": 0x5D,
    "F5": 0x5E, "F6": 0x5F, "F7": 0x60, "F8": 0x61,
    "F9": 0x62, "F10": 0x63, "F11": 0x64, "F12": 0x65,
    "F13": 0x66, "F14": 0x67, "F15": 0x68, "F16": 0x69,
}

# Button codes (from PHKeyCode.V)
BUTTON_M1 = 0x22  # Right back paddle
BUTTON_M2 = 0x23  # Left back paddle

# Function codes (from PHFuncCode.V)
FUNC_XBOX = 0x01
FUNC_KEYBOARD = 0x02
FUNC_MACRO = 0x03
FUNC_TURBO = 0x04
FUNC_SECOND_FUNC = 0x05  # Default — duplicates Y/B


def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    """Generate v1-framed HID command: [CID][3F][idx][payload][pad][3F][CID]"""
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])


def find_vendor_hidraw():
    """Find the vendor HID device (1A86:FE00, usage page 0xFF00)."""
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
        # Check for vendor usage page (0xFF00)
        rd_path = os.path.join(sysfs_path, "device", "report_descriptor")
        if not os.path.exists(rd_path):
            continue
        with open(rd_path, "rb") as f:
            rd = f.read(3)
        if len(rd) >= 3 and rd[0] == 0x06 and rd[1] == 0x00 and rd[2] == 0xFF:
            name = os.path.basename(sysfs_path)
            return f"/dev/{name}"
    return None


def hid_write(fd, data):
    """Write to HID device and return bytes written."""
    try:
        return os.write(fd, data)
    except OSError as e:
        print(f"  Write error: {e}")
        return -1


def hid_read(fd, timeout_ms=500, size=64):
    """Read from HID device with timeout. Returns data or None."""
    ready, _, _ = select.select([fd], [], [], timeout_ms / 1000.0)
    if fd in ready:
        try:
            return os.read(fd, size)
        except OSError as e:
            print(f"  Read error: {e}")
    return None


def build_b4_page2(m1_func, m1_v1, m1_v2, m2_func, m2_v1, m2_v2, preset=0x01):
    """Build a B4 key mapping packet (page 2) with M1/M2 config.

    Standard buttons (BACK through RIGHT) keep their default Xbox mappings.
    Only M1 and M2 are customized.
    """
    entries = [
        # Standard buttons — identity map to Xbox
        [0x0A, FUNC_XBOX, 0x0A, 0x00, 0x00, 0x00],  # BACK
        [0x0B, FUNC_XBOX, 0x0B, 0x00, 0x00, 0x00],  # L3
        [0x0C, FUNC_XBOX, 0x0C, 0x00, 0x00, 0x00],  # R3
        [0x0D, FUNC_XBOX, 0x0D, 0x00, 0x00, 0x00],  # UP
        [0x0E, FUNC_XBOX, 0x0E, 0x00, 0x00, 0x00],  # DOWN
        [0x0F, FUNC_XBOX, 0x0F, 0x00, 0x00, 0x00],  # LEFT
        [0x10, FUNC_XBOX, 0x10, 0x00, 0x00, 0x00],  # RIGHT
        # Back paddles — customized
        [BUTTON_M1, m1_func, m1_v1, m1_v2, 0x00, 0x00],
        [BUTTON_M2, m2_func, m2_v1, m2_v2, 0x00, 0x00],
    ]
    # Flatten entries
    payload = []
    for e in entries:
        payload.extend(e)
    # Header: [idx=0x02] [len=0x38] [flags=0x20] [page=0x02] [preset]
    cmd = [0x02, 0x38, 0x20, 0x02, preset] + payload
    return gen_cmd_v1(0xB4, cmd)


def build_b4_page1(preset=0x01):
    """Build a B4 key mapping packet (page 1) — standard buttons."""
    entries = [
        [0x01, FUNC_XBOX, 0x01, 0x00, 0x00, 0x00],  # A
        [0x02, FUNC_XBOX, 0x02, 0x00, 0x00, 0x00],  # B
        [0x03, FUNC_XBOX, 0x03, 0x00, 0x00, 0x00],  # X
        [0x04, FUNC_XBOX, 0x04, 0x00, 0x00, 0x00],  # Y
        [0x05, FUNC_XBOX, 0x05, 0x00, 0x00, 0x00],  # LB
        [0x06, FUNC_XBOX, 0x06, 0x00, 0x00, 0x00],  # RB
        [0x07, FUNC_XBOX, 0x07, 0x00, 0x00, 0x00],  # LT
        [0x08, FUNC_XBOX, 0x08, 0x00, 0x00, 0x00],  # RT
        [0x09, FUNC_XBOX, 0x09, 0x00, 0x00, 0x00],  # START
    ]
    payload = []
    for e in entries:
        payload.extend(e)
    cmd = [0x02, 0x38, 0x20, 0x01, preset] + payload
    return gen_cmd_v1(0xB4, cmd)


def main():
    print("=== OXP Apex Firmware Remap Test ===\n")

    dev_path = find_vendor_hidraw()
    if not dev_path:
        print("ERROR: Could not find vendor hidraw device (1A86:FE00, usage 0xFF00)")
        print("Make sure the device is connected and you're running as root.")
        sys.exit(1)

    print(f"Found vendor HID device: {dev_path}")

    fd = os.open(dev_path, os.O_RDWR | os.O_NONBLOCK)
    print(f"Opened {dev_path} (fd={fd})\n")

    # === Test 1: Try to read current key mapping ===
    print("--- Test 1: Read current key mapping (B4 query) ---")

    # Try various B4 read commands
    read_cmds = [
        ("B4 empty", gen_cmd_v1(0xB4, [])),
        ("B4 idx=0x00", gen_cmd_v1(0xB4, [], idx=0x00)),
        ("B4 with header", gen_cmd_v1(0xB4, [0x02, 0x38, 0x20])),
    ]

    for label, cmd in read_cmds:
        print(f"\n  Sending {label}: {cmd.hex()}")
        w = hid_write(fd, cmd)
        print(f"  Written: {w} bytes")

        # Read responses (might get multiple packets)
        for _ in range(5):
            resp = hid_read(fd, timeout_ms=500)
            if resp:
                print(f"  << Response ({len(resp)}b): {resp.hex()}")
                # Decode if it's a B4 response
                if len(resp) >= 8 and resp[0] == 0xB4:
                    page = resp[6]
                    preset = resp[7]
                    print(f"     B4 page={page} preset={preset}")
                    # Decode button entries
                    for i in range(8, min(len(resp)-2, 62), 6):
                        btn = resp[i]
                        func = resp[i+1]
                        v1 = resp[i+2]
                        v2 = resp[i+3]
                        if btn == 0x00:
                            break
                        func_name = {1:"XBOX", 2:"KEYBOARD", 3:"MACRO", 4:"TURBO", 5:"SECOND_FUNC"}.get(func, f"0x{func:02x}")
                        if btn == 0x22:
                            print(f"     >>> M1: func={func_name} v1=0x{v1:02x} v2=0x{v2:02x}")
                        elif btn == 0x23:
                            print(f"     >>> M2: func={func_name} v1=0x{v1:02x} v2=0x{v2:02x}")
                        else:
                            print(f"     btn=0x{btn:02x}: func={func_name} v1=0x{v1:02x} v2=0x{v2:02x}")
            else:
                break

    # === Test 2: B2 init then B4 read ===
    print("\n\n--- Test 2: B2 init then B4 read ---")

    # Send B2 init (from captured sequence)
    b2_init = gen_cmd_v1(0xB2, [0x01, 0x1F, 0x40, 0x03, 0x02, 0x03, 0x00, 0x00, 0x00, 0x01])
    print(f"\n  Sending B2 init: {b2_init.hex()}")
    hid_write(fd, b2_init)

    for _ in range(5):
        resp = hid_read(fd, timeout_ms=500)
        if resp:
            cid = resp[0] if resp else 0
            print(f"  << Response CID=0x{cid:02X} ({len(resp)}b): {resp.hex()}")

    # Now try B4 read
    b4_read = gen_cmd_v1(0xB4, [])
    print(f"\n  Sending B4 read: {b4_read.hex()}")
    hid_write(fd, b4_read)

    for _ in range(5):
        resp = hid_read(fd, timeout_ms=500)
        if resp:
            cid = resp[0] if resp else 0
            print(f"  << Response CID=0x{cid:02X} ({len(resp)}b): {resp.hex()}")

    # Disable B2 intercept
    b2_disable = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])
    print(f"\n  Sending B2 disable: {b2_disable.hex()}")
    hid_write(fd, b2_disable)
    time.sleep(0.1)

    # Drain
    for _ in range(5):
        resp = hid_read(fd, timeout_ms=200)
        if resp:
            print(f"  << Drain ({len(resp)}b): {resp.hex()}")

    # === Test 3: Write key mapping (M1->F13, M2->F14) ===
    print("\n\n--- Test 3: Write key mapping (M1->F13, M2->F14) ---")

    # Send page 1 (standard buttons)
    page1 = build_b4_page1(preset=0x01)
    print(f"\n  Sending B4 page 1: {page1.hex()}")
    w = hid_write(fd, page1)
    print(f"  Written: {w} bytes")
    time.sleep(0.1)

    for _ in range(5):
        resp = hid_read(fd, timeout_ms=500)
        if resp:
            print(f"  << Response ({len(resp)}b): {resp.hex()}")

    # Send page 2 (with M1->F13, M2->F14)
    page2 = build_b4_page2(
        m1_func=FUNC_KEYBOARD, m1_v1=0x01, m1_v2=KEY_CODES["F13"],
        m2_func=FUNC_KEYBOARD, m2_v1=0x01, m2_v2=KEY_CODES["F14"],
        preset=0x01,
    )
    print(f"\n  Sending B4 page 2: {page2.hex()}")
    w = hid_write(fd, page2)
    print(f"  Written: {w} bytes")
    time.sleep(0.1)

    for _ in range(5):
        resp = hid_read(fd, timeout_ms=500)
        if resp:
            print(f"  << Response ({len(resp)}b): {resp.hex()}")

    # === Test 4: Read back to verify ===
    print("\n\n--- Test 4: Read back mapping ---")
    b4_read = gen_cmd_v1(0xB4, [])
    print(f"  Sending B4 read: {b4_read.hex()}")
    hid_write(fd, b4_read)

    for _ in range(5):
        resp = hid_read(fd, timeout_ms=500)
        if resp:
            print(f"  << Response ({len(resp)}b): {resp.hex()}")

    # === Test 5: Reset to default (SECOND_FUNC) ===
    if "--reset" in sys.argv:
        print("\n\n--- Test 5: Reset M1/M2 to default (SECOND_FUNC) ---")
        page2_default = build_b4_page2(
            m1_func=FUNC_SECOND_FUNC, m1_v1=0x00, m1_v2=0x00,
            m2_func=FUNC_SECOND_FUNC, m2_v1=0x00, m2_v2=0x00,
            preset=0x01,
        )
        print(f"  Sending B4 page 2 (default): {page2_default.hex()}")
        hid_write(fd, page2_default)
        time.sleep(0.1)
        for _ in range(5):
            resp = hid_read(fd, timeout_ms=500)
            if resp:
                print(f"  << Response ({len(resp)}b): {resp.hex()}")

    os.close(fd)
    print("\n\nDone. Press back paddles to test — they should now send F13/F14")
    print("as keyboard events (check with 'evtest' or 'libinput debug-events').")
    print("\nTo reset to default: sudo python3 test-firmware-remap.py --reset")


if __name__ == "__main__":
    main()
