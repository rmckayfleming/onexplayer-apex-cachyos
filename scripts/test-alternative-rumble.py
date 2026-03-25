#!/usr/bin/env python3
"""Test alternative rumble approaches while in intercept mode.

Tests:
  1. Alternative CIDs (0xB0, 0xB1, 0xB4-0xB9) with vibration payloads
  2. 0xB3 with minimal/stripped payloads
  3. 0xB3 with different sub-command bytes

The goal is to find a rumble command that works WITHOUT causing the
firmware to exit intercept mode.

Run as root with HHD stopped:
    sudo systemctl stop hhd@$(whoami)
    sudo python3 scripts/test-alternative-rumble.py
    sudo systemctl start hhd@$(whoami)
"""

import glob
import os
import select
import sys
import time


# ---------------------------------------------------------------------------
# Device discovery
# ---------------------------------------------------------------------------

def find_vendor_hidraw():
    """Find the OXP vendor HID device (1A86:FE00) with usage page 0xFF00."""
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
            name = os.path.basename(sysfs_path)
            return f"/dev/{name}"
    return None


# ---------------------------------------------------------------------------
# Command generators
# ---------------------------------------------------------------------------

def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    """V1 framing: [cid, 0x3F, idx, *cmd, padding, 0x3F, cid]"""
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])


def gen_cmd_v2(cid, cmd, size=64):
    """V2 framing: [cid, 0xFF, *cmd, padding...]"""
    c = bytes(cmd) if isinstance(cmd, list) else cmd
    base = bytes([cid, 0xFF]) + c
    return base + bytes([0] * (size - len(base)))


def gen_intercept_enable():
    return gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])


def gen_intercept_disable():
    return gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])


# The standard 0xB3 vibration payload (mode=start, strength=255)
STANDARD_VIB_PAYLOAD = [
    0x02, 0x38, 0x02, 0xE3, 0x39, 0xE3, 0x39, 0xE3, 0x39,
    0x01, 0xFF, 0xFF, 0xE3, 0x39, 0xE3,
] + [0x00] * 35 + [
    0x39, 0xE3, 0x39, 0xE3, 0xE3, 0x02, 0x04, 0x39, 0x39,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def drain(fd, duration=0.3):
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        r, _, _ = select.select([fd], [], [], 0.05)
        if fd in r:
            os.read(fd, 64)


def check_intercept_alive(fd, timeout=1.5):
    """Check if intercept packets (0xB2) are still coming."""
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


def test_rumble_cmd(fd, desc, cmd_bytes):
    """Send a rumble command and check if intercept survives.

    Returns (intercept_alive, description).
    """
    print(f"\n  --- {desc} ---")
    print(f"  Sending: {cmd_bytes[:16].hex()}{'...' if len(cmd_bytes) > 16 else ''}")

    os.write(fd, cmd_bytes)
    time.sleep(0.2)

    alive = check_intercept_alive(fd, timeout=1.5)
    status = "ALIVE" if alive else "DEAD"
    print(f"  Intercept: {status}")

    if alive:
        print(f"  >>> DO YOU FEEL VIBRATION? <<<")
        time.sleep(1)
    else:
        # Re-enable intercept for next test
        print(f"  Intercept exited! Re-enabling...")
        os.write(fd, gen_intercept_enable())
        time.sleep(0.5)
        re_alive = check_intercept_alive(fd, timeout=1.5)
        if not re_alive:
            print(f"  WARNING: Could not re-enable intercept!")
        drain(fd)

    return alive


# ---------------------------------------------------------------------------
# Test suites
# ---------------------------------------------------------------------------

def build_test_suite():
    """Build the list of rumble commands to test."""
    tests = []

    # Group 1: Alternative CIDs with standard vibration payload (v1 framing)
    for cid in [0xB0, 0xB1, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8, 0xB9]:
        tests.append((
            f"CID 0x{cid:02X} + standard payload (v1 framing)",
            gen_cmd_v1(cid, STANDARD_VIB_PAYLOAD),
        ))

    # Group 2: 0xB3 with minimal payloads (v1 framing)
    tests.append((
        "0xB3 minimal: [0x01, 0xFF, 0xFF]",
        gen_cmd_v1(0xB3, [0x01, 0xFF, 0xFF]),
    ))
    tests.append((
        "0xB3 minimal: [0x01, 0xFF, 0xFF, 0x00, 0x00]",
        gen_cmd_v1(0xB3, [0x01, 0xFF, 0xFF, 0x00, 0x00]),
    ))
    tests.append((
        "0xB3 no filler: mode+strength only",
        gen_cmd_v1(0xB3, [0x02, 0x38, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                          0x01, 0xFF, 0xFF]),
    ))

    # Group 3: 0xB3 with v2 framing (0xFF header) — different payload sizes
    tests.append((
        "0xB3 v2 framing: [0x01, 0xFF, 0xFF]",
        gen_cmd_v2(0xB3, [0x01, 0xFF, 0xFF]),
    ))
    tests.append((
        "0xB3 v2 framing: full standard payload",
        gen_cmd_v2(0xB3, STANDARD_VIB_PAYLOAD),
    ))

    # Group 4: Alternative CIDs with v2 framing
    for cid in [0xB0, 0xB1, 0xB4, 0xB5]:
        tests.append((
            f"CID 0x{cid:02X} + minimal payload (v2 framing)",
            gen_cmd_v2(cid, [0x01, 0xFF, 0xFF]),
        ))

    # Group 5: 0xB3 with different sub-command byte (byte[0] of payload)
    for sub in [0x00, 0x01, 0x03, 0x04, 0x05]:
        payload = list(STANDARD_VIB_PAYLOAD)
        payload[0] = sub  # Change the first byte
        tests.append((
            f"0xB3 sub-cmd=0x{sub:02X} (standard payload)",
            gen_cmd_v1(0xB3, payload),
        ))

    return tests


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if os.geteuid() != 0:
        print("ERROR: Run as root (sudo)")
        sys.exit(1)

    vendor_path = find_vendor_hidraw()
    if not vendor_path:
        print("ERROR: Vendor HID device (1A86:FE00) not found")
        sys.exit(1)
    print(f"Vendor HID: {vendor_path}")

    fd = os.open(vendor_path, os.O_RDWR)
    print(f"Opened vendor HID (fd={fd})")

    tests = build_test_suite()
    print(f"\n{'='*60}")
    print(f"ALTERNATIVE RUMBLE COMMAND EXPLORATION")
    print(f"{'='*60}")
    print(f"\nWill test {len(tests)} rumble commands while intercept is active.")
    print("For each test: check if you feel vibration AND if intercept survives.")
    print("\nPress Enter to start...")
    input()

    # Enable intercept
    print("Enabling intercept mode...")
    os.write(fd, gen_intercept_enable())
    time.sleep(1)
    alive = check_intercept_alive(fd)
    if not alive:
        print("ERROR: Intercept mode did not activate")
        os.close(fd)
        sys.exit(1)
    print("Intercept mode ACTIVE")
    drain(fd)

    results = []
    for desc, cmd_bytes in tests:
        try:
            survived = test_rumble_cmd(fd, desc, cmd_bytes)
            results.append((desc, survived))
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append((desc, None))
            # Try to recover intercept
            os.write(fd, gen_intercept_enable())
            time.sleep(0.5)
            drain(fd)

    # Cleanup
    print(f"\n{'='*60}")
    print("CLEANUP")
    print(f"{'='*60}")
    os.write(fd, gen_intercept_disable())
    time.sleep(0.3)
    os.close(fd)

    # Summary
    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    print(f"\n{'Description':<55} {'Intercept'}")
    print("-" * 70)
    for desc, survived in results:
        status = "ALIVE" if survived else ("DEAD" if survived is False else "ERROR")
        flag = " <<< POSSIBLE WIN" if survived else ""
        print(f"  {desc:<53} {status}{flag}")

    winners = [(d, s) for d, s in results if s is True]
    if winners:
        print(f"\n*** {len(winners)} COMMAND(S) KEPT INTERCEPT ALIVE ***")
        print("Did any of them produce vibration?")
        print("If YES → we have a rumble command that works in intercept mode!")
    else:
        print("\nAll commands either killed intercept or errored.")
        print("No alternative rumble command found.")


if __name__ == "__main__":
    main()
