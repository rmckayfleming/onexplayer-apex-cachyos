#!/usr/bin/env python3
"""Monitor ALL vendor HID events to identify button key codes.

Usage: sudo python3 scripts/monitor-button-events.py

Monitors event5, event6, event7, event8 (all 1a86:fe00 interfaces)
plus event2 (AT keyboard) simultaneously.

Press buttons on the device and watch what key codes appear.
Ctrl+C to stop.
"""

import struct
import os
import select
import signal
import sys

DEVICES = {
    "/dev/input/event2": "AT Keyboard",
    "/dev/input/event5": "HID 1a86:fe00 (kbd)",
    "/dev/input/event6": "HID 1a86:fe00 (mouse)",
    "/dev/input/event7": "HID 1a86:fe00 (consumer)",
    "/dev/input/event8": "HID 1a86:fe00 (sysctl)",
}

TYPE_NAMES = {1: "KEY", 2: "REL", 3: "ABS", 4: "MSC"}
KEY_NAMES = {
    29: "LCTRL", 56: "LALT", 97: "RCTRL", 100: "RALT",
    125: "LGUI", 32: "D", 34: "G", 24: "O", 99: "SYSRQ",
    111: "DELETE", 183: "F13", 184: "F14", 116: "POWER",
    142: "SLEEP", 113: "MUTE", 114: "VOLDOWN", 115: "VOLUP",
    148: "PROG1", 149: "PROG2", 150: "PROG3", 151: "PROG4",
    172: "HOMEPAGE", 217: "SEARCH", 240: "UNKNOWN",
}


def main():
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    fds = {}
    poll = select.poll()

    for path, name in DEVICES.items():
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            fds[fd] = (path, name)
            poll.register(fd, select.POLLIN)
            print(f"  Opened {path} ({name})")
        except (PermissionError, OSError) as e:
            print(f"  SKIP   {path} ({name}): {e}")

    if not fds:
        print("\nNo devices opened! Run with sudo.")
        sys.exit(1)

    print("\nListening for events. Press buttons now! Ctrl+C to stop.\n")

    try:
        while True:
            for fd, mask in poll.poll(200):
                try:
                    data = os.read(fd, 24)
                except OSError:
                    continue
                if len(data) == 24:
                    sec, usec, typ, code, val = struct.unpack("llHHi", data)
                    if typ != 0:
                        path, name = fds[fd]
                        type_name = TYPE_NAMES.get(typ, str(typ))
                        key_name = KEY_NAMES.get(code, str(code))
                        state = {0: "UP", 1: "DOWN", 2: "REPEAT"}.get(val, str(val))
                        print(f"  [{name:30s}]  {type_name:>4}  code={code:<4} ({key_name:<10})  {state}")
    finally:
        for fd in fds:
            os.close(fd)


if __name__ == "__main__":
    main()
