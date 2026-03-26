#!/usr/bin/env python3
"""Probe the vendor HID for button remap commands.

Goal: Find the CID and payload format for firmware-level button remapping
(as used by OneXConsole's setKeyMappingInfo) so we can remap M1/M2 to
keyboard keys WITHOUT intercept mode.

Approach:
  1. Monitor all hidraw devices for any changes
  2. Send candidate remap commands with different CIDs
  3. After each command, prompt user to press M1 — see if behavior changes

Run as root with HHD stopped:
    sudo systemctl stop hhd@$(whoami)
    sudo python3 scripts/probe-remap.py
    sudo systemctl start hhd@$(whoami)
"""

import glob
import os
import select
import struct
import sys
import time


# ---------------------------------------------------------------------------
# Device discovery
# ---------------------------------------------------------------------------

def find_vendor_hidraw():
    """Find OXP vendor HID (1A86:FE00) with usage page 0xFF00."""
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


def find_all_vendor_hidraw():
    """Find ALL hidraw interfaces for the vendor device (1A86:FE00)."""
    devices = []
    for sysfs_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent_path = os.path.join(sysfs_path, "device", "uevent")
        if not os.path.exists(uevent_path):
            continue
        with open(uevent_path) as f:
            content = f.read()
        vid = pid = 0
        hid_name = ""
        for line in content.splitlines():
            if line.startswith("HID_ID="):
                parts = line.split(":")
                if len(parts) >= 3:
                    vid = int(parts[1], 16)
                    pid = int(parts[2], 16)
            if line.startswith("HID_NAME="):
                hid_name = line.split("=", 1)[1]
        if vid != 0x1A86 or pid != 0xFE00:
            continue
        name = os.path.basename(sysfs_path)
        # Check usage page
        rd_path = os.path.join(sysfs_path, "device", "report_descriptor")
        usage = "unknown"
        if os.path.exists(rd_path):
            with open(rd_path, "rb") as f:
                rd = f.read(3)
            if len(rd) >= 3:
                if rd[0] == 0x06 and rd[1] == 0x00 and rd[2] == 0xFF:
                    usage = "vendor(0xFF00)"
                elif rd[0] == 0x05 and rd[1] == 0x01:
                    usage = "generic_desktop(0x01)"
        devices.append((f"/dev/{name}", hid_name, usage))
    return devices


# ---------------------------------------------------------------------------
# Command generator
# ---------------------------------------------------------------------------

def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    """V1 framing: [cid, 0x3F, idx, *cmd, padding, 0x3F, cid]"""
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])


# ---------------------------------------------------------------------------
# Known constants from OneXConsole RE
# ---------------------------------------------------------------------------

# Button codes
M1_CODE = 0x22  # Right paddle
M2_CODE = 0x23  # Left paddle
HOME_CODE = 0x21
KB_CODE = 0x24

# Function codes
FUNC_XBOX = 0x01
FUNC_KEYBOARD = 0x02
FUNC_MACRO = 0x03
FUNC_TURBO = 0x04
FUNC_SECOND = 0x05  # Default

# Keyboard sub-types (value1 when funcCode=02)
KBTYPE_KEY = 0x01
KBTYPE_PREACTION = 0x02
KBTYPE_KEYPAD = 0x03
KBTYPE_MACRO = 0x04

# HID keyboard scancodes for testing
KEY_F13 = 0x68  # Unlikely to conflict
KEY_F14 = 0x69
KEY_F15 = 0x6A


# ---------------------------------------------------------------------------
# Phase 1: Baseline — what do M1/M2 send without intercept?
# ---------------------------------------------------------------------------

def monitor_all_hidraw(duration=5.0, label=""):
    """Open all hidraw devices and print any events for duration seconds."""
    print(f"\n{'='*60}")
    print(f"  MONITORING ALL HIDRAW ({label})")
    print(f"  Press M1 (right paddle) and M2 (left paddle) now!")
    print(f"  Monitoring for {duration}s...")
    print(f"{'='*60}")

    fds = {}
    for i in range(20):
        path = f"/dev/hidraw{i}"
        if not os.path.exists(path):
            continue
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            # Get name
            try:
                uevent = f"/sys/class/hidraw/hidraw{i}/device/uevent"
                with open(uevent) as f:
                    name = "?"
                    for line in f:
                        if line.startswith("HID_NAME="):
                            name = line.strip().split("=", 1)[1]
                fds[fd] = (path, name)
            except:
                fds[fd] = (path, "?")
        except:
            pass

    if not fds:
        print("No devices opened!")
        return

    poll = select.poll()
    for fd in fds:
        poll.register(fd, select.POLLIN)

    last_report = {}
    start = time.time()
    event_count = 0

    try:
        while time.time() - start < duration:
            events = poll.poll(200)
            for fd, mask in events:
                if mask & select.POLLIN:
                    try:
                        data = os.read(fd, 256)
                        path, name = fds[fd]
                        hex_data = data.hex()
                        key = fd
                        if last_report.get(key) != hex_data:
                            last_report[key] = hex_data
                            pretty = " ".join(f"{b:02x}" for b in data)
                            short_name = path.split("/")[-1]
                            print(f"  [{short_name}] {name[:30]:30s} len={len(data):3d}: {pretty[:90]}")
                            event_count += 1
                    except BlockingIOError:
                        pass
    except KeyboardInterrupt:
        pass
    finally:
        for fd in fds:
            try:
                os.close(fd)
            except:
                pass

    if event_count == 0:
        print("  (no events detected)")
    print()


# ---------------------------------------------------------------------------
# Phase 2: CID scan — send commands, look for ACKs
# ---------------------------------------------------------------------------

def cid_scan(vendor_path):
    """Send probe commands with different CIDs and read any responses."""
    print(f"\n{'='*60}")
    print(f"  PHASE 2: CID SCAN")
    print(f"  Sending probe commands to {vendor_path}")
    print(f"  Looking for ACK responses...")
    print(f"{'='*60}\n")

    # CIDs to try (skip known ones)
    known_cids = {0x07: "RGB", 0xB2: "intercept", 0xB3: "motor", 0xF5: "init"}
    # Try a broad range
    test_cids = list(range(0x01, 0x10)) + list(range(0xA0, 0xC0)) + list(range(0xF0, 0x100))
    # Remove known ones
    test_cids = [c for c in test_cids if c not in known_cids]

    fd = os.open(vendor_path, os.O_RDWR | os.O_NONBLOCK)

    responsive_cids = []

    for cid in test_cids:
        # Simple probe: just the CID with minimal payload
        cmd = gen_cmd_v1(cid, [0x01])
        try:
            os.write(fd, cmd)
        except:
            continue

        # Read response (short timeout)
        poll = select.poll()
        poll.register(fd, select.POLLIN)
        time.sleep(0.05)
        events = poll.poll(50)

        response = None
        for _, mask in events:
            if mask & select.POLLIN:
                try:
                    response = os.read(fd, 256)
                except BlockingIOError:
                    pass

        if response and len(response) > 0:
            pretty = " ".join(f"{b:02x}" for b in response[:20])
            print(f"  CID 0x{cid:02X}: RESPONSE len={len(response)}: {pretty}...")
            responsive_cids.append(cid)
        # Drain any extra data
        try:
            while True:
                os.read(fd, 256)
        except:
            pass

    os.close(fd)

    print(f"\n  Responsive CIDs: {[f'0x{c:02X}' for c in responsive_cids]}")
    print(f"  Known CIDs for reference: {', '.join(f'0x{k:02X}={v}' for k,v in known_cids.items())}")
    return responsive_cids


# ---------------------------------------------------------------------------
# Phase 3: Try remap payloads on responsive CIDs
# ---------------------------------------------------------------------------

def try_remap(vendor_path, cid, button=M1_CODE, target_key=KEY_F13):
    """Try sending a remap command with a given CID.

    We try several payload formats since we don't know the exact structure.
    """
    payloads = [
        # Format 1: [button, func, subtype, key]
        [button, FUNC_KEYBOARD, KBTYPE_KEY, target_key],
        # Format 2: [mode, button, func, subtype, key] (mode 1)
        [0x01, button, FUNC_KEYBOARD, KBTYPE_KEY, target_key],
        # Format 3: [mode, button, func, subtype, key] (mode 2)
        [0x02, button, FUNC_KEYBOARD, KBTYPE_KEY, target_key],
        # Format 4: [button, func, key] (simpler)
        [button, FUNC_KEYBOARD, target_key],
        # Format 5: [button, key] (simplest)
        [button, target_key],
        # Format 6: OneXConsole uses mode 1/2 with delays — maybe mode is first byte
        [0x01, button, FUNC_KEYBOARD, KBTYPE_KEY, 0x00, target_key],
        # Format 7: maybe button count first
        [0x01, button, 0x02, 0x01, target_key, 0x00],
    ]

    fd = os.open(vendor_path, os.O_RDWR | os.O_NONBLOCK)

    print(f"\n  Testing CID 0x{cid:02X} with {len(payloads)} payload formats...")
    print(f"  Target: remap M1 (0x{button:02X}) → F13 (0x{target_key:02X})")

    for i, payload in enumerate(payloads):
        cmd = gen_cmd_v1(cid, payload)
        pretty_payload = " ".join(f"{b:02x}" for b in payload)
        try:
            os.write(fd, cmd)
        except Exception as e:
            print(f"    Format {i+1} [{pretty_payload}]: write error: {e}")
            continue

        time.sleep(0.1)

        # Read response
        poll = select.poll()
        poll.register(fd, select.POLLIN)
        events = poll.poll(100)
        response = None
        for _, mask in events:
            if mask & select.POLLIN:
                try:
                    response = os.read(fd, 256)
                except BlockingIOError:
                    pass

        if response:
            pretty = " ".join(f"{b:02x}" for b in response[:20])
            print(f"    Format {i+1} [{pretty_payload}]: ACK → {pretty}")
        else:
            print(f"    Format {i+1} [{pretty_payload}]: no response")

        # Drain
        try:
            while True:
                os.read(fd, 256)
        except:
            pass

        time.sleep(0.35)  # OneXConsole uses 450ms delays

    os.close(fd)


# ---------------------------------------------------------------------------
# Phase 4: Factory reset (restore defaults)
# ---------------------------------------------------------------------------

def try_factory_reset(vendor_path, cid):
    """Try sending a factory reset command to restore default mapping."""
    fd = os.open(vendor_path, os.O_RDWR | os.O_NONBLOCK)

    # Try a few reset payloads
    resets = [
        [0xFF],  # Common reset byte
        [0x00],  # Zero = default
        [0x01, 0x00],
        [0x00, 0x00, 0x00],
    ]

    print(f"\n  Trying factory reset on CID 0x{cid:02X}...")
    for payload in resets:
        cmd = gen_cmd_v1(cid, payload)
        try:
            os.write(fd, cmd)
        except:
            pass
        time.sleep(0.1)
        try:
            resp = os.read(fd, 256)
            pretty = " ".join(f"{b:02x}" for b in resp[:16])
            pp = " ".join(f"{b:02x}" for b in payload)
            print(f"    [{pp}]: ACK → {pretty}")
        except:
            pass

    os.close(fd)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if os.geteuid() != 0:
        print("ERROR: Run as root (sudo)")
        sys.exit(1)

    print("="*60)
    print("  OXP Apex — Button Remap Protocol Probe")
    print("="*60)

    # Show all vendor HID interfaces
    print("\nVendor HID interfaces (1A86:FE00):")
    vendor_devices = find_all_vendor_hidraw()
    for path, name, usage in vendor_devices:
        print(f"  {path}: {name} [{usage}]")

    vendor_path = find_vendor_hidraw()
    if not vendor_path:
        print("ERROR: Vendor HID (0xFF00) not found!")
        sys.exit(1)
    print(f"\nVendor command channel: {vendor_path}")

    # Menu
    while True:
        print(f"\n{'─'*40}")
        print("  1) Baseline: monitor all hidraw (no commands)")
        print("  2) CID scan: find responsive command IDs")
        print("  3) Try remap on a specific CID")
        print("  4) Try remap on ALL responsive CIDs")
        print("  5) Monitor after remap attempt")
        print("  6) Factory reset attempt")
        print("  7) Send custom command")
        print("  8) Read current mapping (query CIDs)")
        print("  q) Quit")
        print(f"{'─'*40}")

        try:
            choice = input("  Choice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if choice == "1":
            monitor_all_hidraw(duration=10.0, label="BASELINE — no commands sent")

        elif choice == "2":
            responsive = cid_scan(vendor_path)

        elif choice == "3":
            cid_str = input("  CID (hex, e.g. B4): ").strip()
            try:
                cid = int(cid_str, 16)
            except:
                print("  Invalid hex!")
                continue
            try_remap(vendor_path, cid)
            print("\n  Now press M1 to test if remap worked...")
            monitor_all_hidraw(duration=8.0, label=f"AFTER REMAP CID 0x{cid:02X}")

        elif choice == "4":
            print("  Running CID scan first...")
            responsive = cid_scan(vendor_path)
            for cid in responsive:
                if cid in (0x07, 0xB2, 0xB3, 0xF5):
                    continue
                try_remap(vendor_path, cid)
                print(f"\n  Press M1 to test CID 0x{cid:02X}...")
                monitor_all_hidraw(duration=5.0, label=f"AFTER REMAP CID 0x{cid:02X}")
                cont = input("  Continue to next CID? (y/n): ").strip().lower()
                if cont != "y":
                    break

        elif choice == "5":
            monitor_all_hidraw(duration=10.0, label="POST-REMAP MONITORING")

        elif choice == "6":
            cid_str = input("  CID for reset (hex): ").strip()
            try:
                cid = int(cid_str, 16)
            except:
                print("  Invalid hex!")
                continue
            try_factory_reset(vendor_path, cid)

        elif choice == "7":
            cid_str = input("  CID (hex, e.g. B4): ").strip()
            payload_str = input("  Payload bytes (hex, space-separated, e.g. 22 02 01 68): ").strip()
            try:
                cid = int(cid_str, 16)
                payload = [int(b, 16) for b in payload_str.split()]
            except:
                print("  Invalid input!")
                continue
            cmd = gen_cmd_v1(cid, payload)
            pretty = " ".join(f"{b:02x}" for b in cmd)
            print(f"  Sending: {pretty}")
            fd = os.open(vendor_path, os.O_RDWR | os.O_NONBLOCK)
            os.write(fd, cmd)
            time.sleep(0.1)
            try:
                resp = os.read(fd, 256)
                pretty = " ".join(f"{b:02x}" for b in resp[:32])
                print(f"  Response: {pretty}")
            except BlockingIOError:
                print("  No response")
            os.close(fd)

        elif choice == "8":
            # Try to query current mapping state
            print("\n  Querying firmware state...")
            fd = os.open(vendor_path, os.O_RDWR | os.O_NONBLOCK)

            # Try reading commands (common pattern: send CID with read flag)
            query_cids = list(range(0x01, 0x10)) + list(range(0xA0, 0xC0)) + list(range(0xF0, 0x100))
            for cid in query_cids:
                # Try: [CID] with "read" payloads
                for payload in [[0x00], [0x02], [0x22], [0x23]]:
                    cmd = gen_cmd_v1(cid, payload)
                    try:
                        os.write(fd, cmd)
                    except:
                        continue
                    time.sleep(0.03)
                    try:
                        resp = os.read(fd, 256)
                        if resp:
                            pretty = " ".join(f"{b:02x}" for b in resp[:24])
                            pp = " ".join(f"{b:02x}" for b in payload)
                            print(f"    CID 0x{cid:02X} [{pp}]: {pretty}")
                    except BlockingIOError:
                        pass
                    # Drain
                    try:
                        while True:
                            os.read(fd, 256)
                    except:
                        pass

            os.close(fd)

        elif choice == "q":
            break

    print("\nDone. Remember to restart HHD:")
    print("  sudo systemctl start hhd@$(whoami)")


if __name__ == "__main__":
    main()
