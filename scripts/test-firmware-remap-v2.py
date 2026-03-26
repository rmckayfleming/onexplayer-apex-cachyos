#!/usr/bin/env python3
"""Firmware remap test v2 — systematic approach.

Improvements over v1:
  - Monitors ALL hidraw interfaces simultaneously (keyboard + vendor)
  - Tries init handshake (0xF5) before B4
  - Tries multiple write formats (not just response-mirror)
  - Checks if keyboard hidraw shows paddle events at each stage
  - Uses both OneXConsole key encoding AND standard HID scancodes

Run as root with HHD stopped:
    sudo systemctl stop hhd
    sudo python3 scripts/test-firmware-remap-v2.py
"""

import glob
import os
import select
import sys
import time


# === Device discovery ===

def find_vendor_hidraw_devices():
    """Find ALL hidraw interfaces for 1A86:FE00, with usage page info."""
    devices = []
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
        name = os.path.basename(sysfs_path)
        path = f"/dev/{name}"
        # Read report descriptor for usage page
        rd_path = os.path.join(sysfs_path, "device", "report_descriptor")
        usage_page = "unknown"
        if os.path.exists(rd_path):
            with open(rd_path, "rb") as f:
                rd = f.read(4)
            if len(rd) >= 3:
                if rd[0] == 0x06 and rd[1] == 0x00 and rd[2] == 0xFF:
                    usage_page = "vendor(0xFF00)"
                elif rd[0] == 0x05 and rd[1] == 0x01:
                    usage_page = "generic_desktop(0x01)"
                elif rd[0] == 0x06:
                    usage_page = f"0x{rd[2]:02x}{rd[1]:02x}"
        devices.append((path, usage_page))
    return devices


def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    """V1 framing: [cid, 0x3F, idx, *cmd, padding, 0x3F, cid]"""
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])


def hex_dump(data):
    return " ".join(f"{b:02x}" for b in data)


# === HID keyboard scancode table ===

HID_KEYCODES = {
    0x00: "-", 0x3A: "F1", 0x3B: "F2", 0x3C: "F3", 0x3D: "F4",
    0x3E: "F5", 0x3F: "F6", 0x40: "F7", 0x41: "F8",
    0x42: "F9", 0x43: "F10", 0x44: "F11", 0x45: "F12",
    0x4B: "PageUp", 0x4E: "PageDown",
    0x68: "F13", 0x69: "F14", 0x6A: "F15", 0x6B: "F16",
}


def decode_kbd_report(data):
    """Decode 8-byte HID keyboard report."""
    if len(data) != 8:
        return None
    mod = data[0]
    keys = [data[i] for i in range(2, 8) if data[i] != 0]
    if not keys and not mod:
        return None  # all-zero = idle
    key_names = [HID_KEYCODES.get(k, f"0x{k:02x}") for k in keys]
    return f"mod=0x{mod:02x} keys=[{', '.join(key_names)}]"


# === Monitor class ===

class HidrawMonitor:
    """Opens multiple hidraw devices and reads from them."""

    def __init__(self, paths_and_labels):
        self.fds = {}
        for path, label in paths_and_labels:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                self.fds[fd] = (path, label)
            except OSError as e:
                print(f"  SKIP {path}: {e}")

    def drain(self, timeout_ms=100):
        """Read and discard all pending data."""
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            ready, _, _ = select.select(list(self.fds.keys()), [], [], 0.01)
            if not ready:
                break
            for fd in ready:
                try:
                    os.read(fd, 256)
                except:
                    pass

    def read_events(self, duration_s=3.0, show_idle_kbd=False):
        """Read events for duration, return list of (label, data)."""
        events = []
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select(list(self.fds.keys()), [], [],
                                        min(remaining, 0.05))
            for fd in ready:
                try:
                    data = os.read(fd, 256)
                    path, label = self.fds[fd]
                    # Skip idle keyboard reports (all zeros)
                    if len(data) == 8 and data == b'\x00' * 8:
                        if not show_idle_kbd:
                            continue
                    events.append((label, data))
                except:
                    pass
        return events

    def close(self):
        for fd in self.fds:
            try:
                os.close(fd)
            except:
                pass


# === Main ===

def main():
    if os.geteuid() != 0:
        print("ERROR: Run as root")
        sys.exit(1)

    print("=" * 60)
    print("  OXP Apex — Firmware Remap Test v2")
    print("=" * 60)

    # Find devices
    devices = find_vendor_hidraw_devices()
    if not devices:
        print("ERROR: No vendor HID devices found (1A86:FE00)")
        sys.exit(1)

    vendor_path = None
    kbd_paths = []
    print("\nVendor HID interfaces:")
    for path, usage in devices:
        print(f"  {path}: {usage}")
        if "vendor" in usage or "0xFF00" in usage:
            vendor_path = path
        else:
            kbd_paths.append(path)

    if not vendor_path:
        print("ERROR: Vendor command interface (0xFF00) not found")
        sys.exit(1)

    print(f"\nVendor command channel: {vendor_path}")
    print(f"Keyboard/other interfaces: {kbd_paths}")

    # Open vendor for write
    vendor_fd = os.open(vendor_path, os.O_RDWR | os.O_NONBLOCK)

    # Open monitor on ALL interfaces (including vendor for read)
    mon_targets = [(p, f"kbd:{os.path.basename(p)}") for p in kbd_paths]
    mon_targets.append((vendor_path, f"vendor:{os.path.basename(vendor_path)}"))
    monitor = HidrawMonitor(mon_targets)

    # Drain any stale data
    monitor.drain(200)

    # ================================================================
    # PHASE 0: Check current state — are paddles sending keyboard?
    # ================================================================
    print(f"\n{'='*60}")
    print("  PHASE 0: Baseline — press both paddles (5 seconds)")
    print("  Looking for keyboard events on any hidraw...")
    print(f"{'='*60}")

    events = monitor.read_events(duration_s=5.0)
    if events:
        print(f"\n  Got {len(events)} events:")
        for label, data in events[:20]:
            decoded = decode_kbd_report(data) if len(data) == 8 else None
            if decoded:
                print(f"    [{label}] KBD: {decoded}")
            elif data[0] == 0xB2:
                print(f"    [{label}] B2 response: {hex_dump(data[:16])}")
            else:
                print(f"    [{label}] raw({len(data)}b): {hex_dump(data[:20])}")
    else:
        print("\n  No events. Paddles are silent (as expected if remap lost).")

    # ================================================================
    # PHASE 1: Try B2 to verify write path works
    # ================================================================
    print(f"\n{'='*60}")
    print("  PHASE 1: Verify B2 write path")
    print(f"{'='*60}")

    monitor.drain(100)
    b2_cmd = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
    print(f"  Sending B2 intercept: {hex_dump(b2_cmd[:10])}...")
    os.write(vendor_fd, b2_cmd)
    time.sleep(0.3)

    events = monitor.read_events(duration_s=1.0)
    b2_ok = False
    for label, data in events:
        if data[0] == 0xB2:
            print(f"  B2 response OK: {hex_dump(data[:16])}")
            b2_ok = True
            break
    if not b2_ok:
        print("  WARNING: No B2 response! Write path may be broken.")

    # Disable intercept
    b2_off = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])
    os.write(vendor_fd, b2_off)
    time.sleep(0.2)
    monitor.drain(500)

    # ================================================================
    # PHASE 2: Try 0xF5 init (seen in known CIDs)
    # ================================================================
    print(f"\n{'='*60}")
    print("  PHASE 2: Try init handshakes")
    print(f"{'='*60}")

    init_cmds = [
        ("F5 init [0x01]", gen_cmd_v1(0xF5, [0x01])),
        ("F5 init [0x00]", gen_cmd_v1(0xF5, [0x00])),
        ("F5 init [0x01,0x01]", gen_cmd_v1(0xF5, [0x01, 0x01])),
        ("B4 query []", gen_cmd_v1(0xB4, [])),
        ("B4 query [0x01]", gen_cmd_v1(0xB4, [0x01])),
        ("B4 query idx=0", gen_cmd_v1(0xB4, [], idx=0x00)),
        ("B4 query [0x00,0x01]", gen_cmd_v1(0xB4, [0x00, 0x01])),
        ("B4 query [0x01,0x01]", gen_cmd_v1(0xB4, [0x01, 0x01])),
        ("B4 query [0x01,0x02]", gen_cmd_v1(0xB4, [0x01, 0x02])),
    ]

    for label, cmd in init_cmds:
        monitor.drain(50)
        print(f"\n  Sending {label}")
        os.write(vendor_fd, cmd)
        time.sleep(0.2)
        events = monitor.read_events(duration_s=0.5)
        if events:
            for elabel, data in events[:3]:
                cid = data[0] if data else 0
                print(f"    << [{elabel}] CID=0x{cid:02X} ({len(data)}b): {hex_dump(data[:20])}")
        else:
            print(f"    (no response)")

    # ================================================================
    # PHASE 3: Try multiple B4 write formats
    # ================================================================
    print(f"\n{'='*60}")
    print("  PHASE 3: Try B4 write formats")
    print("  Target: M1(0x22)->F13, M2(0x23)->F14")
    print(f"{'='*60}")

    # Try both OneXConsole encoding and standard HID scancodes
    # OneXConsole: F13=0x66, F14=0x67
    # Standard HID: F13=0x68, F14=0x69

    # Format A: Mirror response format (what v1 tried)
    page2_entries = [
        0x0A, 0x01, 0x0A, 0x00, 0x00, 0x00,  # BACK
        0x0B, 0x01, 0x0B, 0x00, 0x00, 0x00,  # L3
        0x0C, 0x01, 0x0C, 0x00, 0x00, 0x00,  # R3
        0x0D, 0x01, 0x0D, 0x00, 0x00, 0x00,  # UP
        0x0E, 0x01, 0x0E, 0x00, 0x00, 0x00,  # DOWN
        0x0F, 0x01, 0x0F, 0x00, 0x00, 0x00,  # LEFT
        0x10, 0x01, 0x10, 0x00, 0x00, 0x00,  # RIGHT
        0x22, 0x02, 0x01, 0x68, 0x00, 0x00,  # M1 -> F13 (HID scancode)
        0x23, 0x02, 0x01, 0x69, 0x00, 0x00,  # M2 -> F14 (HID scancode)
    ]

    # Different write formats to try
    write_attempts = [
        # Format A: Full response-mirror (idx=0x01)
        ("A: response-mirror idx=1", gen_cmd_v1(0xB4, [0x02, 0x38, 0x20, 0x02, 0x01] + page2_entries)),

        # Format B: With idx=0x00 (maybe write uses different idx)
        ("B: response-mirror idx=0", gen_cmd_v1(0xB4, [0x02, 0x38, 0x20, 0x02, 0x01] + page2_entries, idx=0x00)),

        # Format C: Just the entries, no header (simpler write?)
        ("C: entries-only", gen_cmd_v1(0xB4, [0x02, 0x01] + [0x22, 0x02, 0x01, 0x68, 0x00, 0x00, 0x23, 0x02, 0x01, 0x69, 0x00, 0x00])),

        # Format D: Single button at a time
        ("D: single M1 remap", gen_cmd_v1(0xB4, [0x22, 0x02, 0x01, 0x68, 0x00, 0x00])),

        # Format E: With write flag (0x40 instead of 0x20?)
        ("E: flags=0x40", gen_cmd_v1(0xB4, [0x02, 0x38, 0x40, 0x02, 0x01] + page2_entries)),

        # Format F: idx=0x02 (matching response packet idx)
        ("F: idx=0x02", gen_cmd_v1(0xB4, [0x02, 0x38, 0x20, 0x02, 0x01] + page2_entries, idx=0x02)),

        # Format G: OneXConsole encoding (F13=0x66)
        ("G: OXP encoding F13=0x66", gen_cmd_v1(0xB4, [0x02, 0x38, 0x20, 0x02, 0x01,
            0x0A, 0x01, 0x0A, 0x00, 0x00, 0x00,
            0x0B, 0x01, 0x0B, 0x00, 0x00, 0x00,
            0x0C, 0x01, 0x0C, 0x00, 0x00, 0x00,
            0x0D, 0x01, 0x0D, 0x00, 0x00, 0x00,
            0x0E, 0x01, 0x0E, 0x00, 0x00, 0x00,
            0x0F, 0x01, 0x0F, 0x00, 0x00, 0x00,
            0x10, 0x01, 0x10, 0x00, 0x00, 0x00,
            0x22, 0x02, 0x01, 0x66, 0x00, 0x00,  # M1 -> F13 (OXP encoding)
            0x23, 0x02, 0x01, 0x67, 0x00, 0x00,  # M2 -> F14 (OXP encoding)
        ])),

        # Format H: With 0x01 prefix (set vs get)
        ("H: set prefix [0x01]", gen_cmd_v1(0xB4, [0x01, 0x02, 0x38, 0x20, 0x02, 0x01] + page2_entries)),

        # Format I: Minimal — just M1/M2 with page+preset
        ("I: minimal page+preset+M1M2", gen_cmd_v1(0xB4, [0x02, 0x01,
            0x22, 0x02, 0x01, 0x68, 0x00, 0x00,
            0x23, 0x02, 0x01, 0x69, 0x00, 0x00,
        ])),
    ]

    for label, cmd in write_attempts:
        monitor.drain(50)
        print(f"\n  {label}")
        print(f"    send: {hex_dump(cmd[:24])}...")
        try:
            w = os.write(vendor_fd, cmd)
            print(f"    written: {w} bytes")
        except OSError as e:
            print(f"    WRITE ERROR: {e}")
            continue

        time.sleep(0.3)
        events = monitor.read_events(duration_s=0.5)
        if events:
            for elabel, data in events[:5]:
                cid = data[0] if data else 0
                if len(data) == 8:
                    decoded = decode_kbd_report(data)
                    if decoded:
                        print(f"    << [{elabel}] KBD: {decoded}")
                        continue
                print(f"    << [{elabel}] CID=0x{cid:02X} ({len(data)}b): {hex_dump(data[:24])}")
        else:
            print(f"    (no response)")

    # ================================================================
    # PHASE 4: Check if anything changed — press paddles
    # ================================================================
    print(f"\n{'='*60}")
    print("  PHASE 4: Press both paddles now (8 seconds)")
    print("  Checking if any write took effect...")
    print(f"{'='*60}")

    monitor.drain(100)
    events = monitor.read_events(duration_s=8.0)
    if events:
        print(f"\n  Got {len(events)} events:")
        for label, data in events[:20]:
            decoded = decode_kbd_report(data) if len(data) == 8 else None
            if decoded:
                print(f"    [{label}] KBD: {decoded}")
            else:
                print(f"    [{label}] raw({len(data)}b): {hex_dump(data[:20])}")
    else:
        print("\n  No events. Writes didn't take effect.")

    # Cleanup
    os.close(vendor_fd)
    monitor.close()

    print(f"\n{'='*60}")
    print("  DONE")
    print(f"{'='*60}")
    print("\nRestart HHD: sudo systemctl start hhd")


if __name__ == "__main__":
    main()
