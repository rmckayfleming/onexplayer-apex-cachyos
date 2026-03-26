#!/usr/bin/env python3
"""Monitor the vendor keyboard HID interface for M1/M2 key events.

The baseline test showed hidraw3 (keyboard interface of 1A86:FE00) fires
when M1 is pressed. This script captures EVERY report without filtering
to catch the actual keypress scancode.

Standard USB HID keyboard report format (8 bytes):
  Byte 0: Modifier keys (Ctrl/Shift/Alt/GUI bitmask)
  Byte 1: Reserved (always 0)
  Byte 2-7: Up to 6 simultaneous key scancodes (0 = no key)

Run as root with HHD stopped:
    sudo systemctl stop hhd@$(whoami)
    sudo python3 scripts/monitor-keyboard-hidraw.py
    sudo systemctl start hhd@$(whoami)
"""

import os
import select
import sys
import time

# HID keyboard scancode lookup (subset — add more as needed)
HID_KEYCODES = {
    0x00: "(none)", 0x04: "A", 0x05: "B", 0x06: "C", 0x07: "D",
    0x08: "E", 0x09: "F", 0x0A: "G", 0x0B: "H", 0x0C: "I",
    0x0D: "J", 0x0E: "K", 0x0F: "L", 0x10: "M", 0x11: "N",
    0x12: "O", 0x13: "P", 0x14: "Q", 0x15: "R", 0x16: "S",
    0x17: "T", 0x18: "U", 0x19: "V", 0x1A: "W", 0x1B: "X",
    0x1C: "Y", 0x1D: "Z", 0x1E: "1", 0x1F: "2", 0x20: "3",
    0x21: "4", 0x22: "5", 0x23: "6", 0x24: "7", 0x25: "8",
    0x26: "9", 0x27: "0", 0x28: "Enter", 0x29: "Escape",
    0x2A: "Backspace", 0x2B: "Tab", 0x2C: "Space",
    0x2D: "-", 0x2E: "=", 0x2F: "[", 0x30: "]", 0x31: "\\",
    0x33: ";", 0x34: "'", 0x35: "`", 0x36: ",", 0x37: ".",
    0x38: "/", 0x39: "CapsLock",
    0x3A: "F1", 0x3B: "F2", 0x3C: "F3", 0x3D: "F4",
    0x3E: "F5", 0x3F: "F6", 0x40: "F7", 0x41: "F8",
    0x42: "F9", 0x43: "F10", 0x44: "F11", 0x45: "F12",
    0x46: "PrintScreen", 0x47: "ScrollLock", 0x48: "Pause",
    0x49: "Insert", 0x4A: "Home", 0x4B: "PageUp",
    0x4C: "Delete", 0x4D: "End", 0x4E: "PageDown",
    0x4F: "Right", 0x50: "Left", 0x51: "Down", 0x52: "Up",
    0x53: "NumLock",
    0x64: "\\(non-US)", 0x65: "Application",
    0x68: "F13", 0x69: "F14", 0x6A: "F15", 0x6B: "F16",
    0x6C: "F17", 0x6D: "F18", 0x6E: "F19", 0x6F: "F20",
    0x70: "F21", 0x71: "F22", 0x72: "F23", 0x73: "F24",
    0xE0: "LCtrl", 0xE1: "LShift", 0xE2: "LAlt", 0xE3: "LGUI",
    0xE4: "RCtrl", 0xE5: "RShift", 0xE6: "RAlt", 0xE7: "RGUI",
}

# Modifier bit names
MODIFIER_BITS = [
    (0x01, "LCtrl"), (0x02, "LShift"), (0x04, "LAlt"), (0x08, "LGUI"),
    (0x10, "RCtrl"), (0x20, "RShift"), (0x40, "RAlt"), (0x80, "RGUI"),
]


def decode_modifier(byte):
    mods = []
    for mask, name in MODIFIER_BITS:
        if byte & mask:
            mods.append(name)
    return "+".join(mods) if mods else "(none)"


def decode_keyboard_report(data):
    """Decode an 8-byte HID keyboard report."""
    if len(data) < 8:
        return f"short report ({len(data)} bytes)"

    mod = data[0]
    keys = [data[i] for i in range(2, 8) if data[i] != 0]

    mod_str = decode_modifier(mod)
    if keys:
        key_names = [HID_KEYCODES.get(k, f"0x{k:02X}") for k in keys]
        return f"mod={mod_str}  keys=[{', '.join(key_names)}]"
    else:
        if mod:
            return f"mod={mod_str}  keys=(none)"
        else:
            return "(all released)"


def main():
    if os.geteuid() != 0:
        print("ERROR: Run as root (sudo)")
        sys.exit(1)

    # Find all vendor HID hidraw interfaces
    import glob as globmod
    targets = []
    for sysfs_path in sorted(globmod.glob("/sys/class/hidraw/hidraw*")):
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
        targets.append(f"/dev/{name}")

    if not targets:
        print("ERROR: No vendor HID devices found!")
        sys.exit(1)

    print("=" * 60)
    print("  OXP Apex — Keyboard HID Monitor")
    print("  Monitoring vendor HID interfaces for key events")
    print("=" * 60)

    fds = {}
    for path in targets:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            fds[fd] = path
            print(f"  Opened: {path}")
        except Exception as e:
            print(f"  SKIP: {path} ({e})")

    print(f"\n  Press M1 (right paddle), M2 (left paddle), Home, KB...")
    print(f"  EVERY report will be shown (no filtering)")
    print(f"  Press Ctrl+C to stop\n")

    poll = select.poll()
    for fd in fds:
        poll.register(fd, select.POLLIN)

    report_count = 0
    try:
        while True:
            events = poll.poll(500)
            for fd, mask in events:
                if mask & select.POLLIN:
                    try:
                        data = os.read(fd, 256)
                        path = fds[fd]
                        short = path.split("/")[-1]
                        report_count += 1
                        ts = time.strftime("%H:%M:%S")

                        # Raw hex
                        pretty = " ".join(f"{b:02x}" for b in data)

                        # Try to decode as keyboard report
                        if len(data) == 8:
                            decoded = decode_keyboard_report(data)
                            print(f"  [{ts}] {short} #{report_count:4d} | {pretty} | {decoded}")
                        else:
                            print(f"  [{ts}] {short} #{report_count:4d} | {pretty}")
                    except BlockingIOError:
                        pass
    except KeyboardInterrupt:
        print(f"\n\nStopped. Total reports: {report_count}")
    finally:
        for fd in fds:
            try:
                os.close(fd)
            except:
                pass


if __name__ == "__main__":
    main()
