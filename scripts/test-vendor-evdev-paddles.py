#!/usr/bin/env python3
"""Test if back paddles produce evdev events WITHOUT intercept mode.

The vendor device (1a86:fe00) creates multiple evdev interfaces:
  - Keyboard (event6)
  - Mouse (event9)
  - Consumer Control (event10)
  - System Control (event11)

Previous testing only checked the hidraw interface. This script monitors
ALL evdev devices from the vendor to see if L4/R4 produce any events
in normal (non-intercept) mode.

If L4/R4 produce unique evdev events, we don't need intercept at all —
Xbox gamepad handles sticks/triggers/rumble natively, and we just grab
the vendor evdev for paddle buttons.

Run as root with HHD stopped:
    sudo systemctl stop hhd@$(whoami)
    sudo python3 scripts/test-vendor-evdev-paddles.py
    sudo systemctl start hhd@$(whoami)
"""

import os
import select
import sys
import time

try:
    import evdev
    from evdev import ecodes, categorize
except ImportError:
    print("ERROR: python-evdev required. Install with: pip install evdev")
    sys.exit(1)


def find_vendor_evdev_devices():
    """Find ALL evdev devices from vendor 1A86 (QinHeng / OXP)."""
    devices = []
    for path in sorted(evdev.list_devices()):
        dev = evdev.InputDevice(path)
        if dev.info.vendor == 0x1A86:
            devices.append(dev)
        else:
            dev.close()
    return devices


def find_xbox_evdev():
    """Find the Xbox 360 gamepad evdev device."""
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if dev.info.vendor == 0x045E and dev.info.product == 0x028E:
            return dev
        dev.close()
    return None


def find_keyboard_evdev():
    """Find the XFLY keyboard evdev device (1A2C:B001)."""
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if dev.info.vendor == 0x1A2C and dev.info.product == 0xB001:
            return dev
        dev.close()
    return None


def describe_event(ev):
    """Human-readable event description."""
    if ev.type == ecodes.EV_KEY:
        code_name = ecodes.KEY.get(ev.code, ecodes.BTN.get(ev.code, f"0x{ev.code:04x}"))
        state = {0: "release", 1: "press", 2: "repeat"}.get(ev.value, str(ev.value))
        return f"KEY {code_name} {state}"
    elif ev.type == ecodes.EV_ABS:
        code_name = ecodes.ABS.get(ev.code, f"0x{ev.code:04x}")
        return f"ABS {code_name} = {ev.value}"
    elif ev.type == ecodes.EV_REL:
        code_name = ecodes.REL.get(ev.code, f"0x{ev.code:04x}")
        return f"REL {code_name} = {ev.value}"
    elif ev.type == ecodes.EV_MSC:
        code_name = ecodes.MSC.get(ev.code, f"0x{ev.code:04x}")
        return f"MSC {code_name} = {ev.value}"
    elif ev.type == ecodes.EV_SYN:
        return None  # Skip SYN events
    else:
        return f"type=0x{ev.type:04x} code=0x{ev.code:04x} value={ev.value}"


def main():
    if os.geteuid() != 0:
        print("ERROR: Run as root (sudo)")
        sys.exit(1)

    print("="*60)
    print("VENDOR EVDEV PADDLE DETECTION (NO INTERCEPT)")
    print("="*60)

    # Find all relevant devices
    vendor_devs = find_vendor_evdev_devices()
    xbox_dev = find_xbox_evdev()
    kb_dev = find_keyboard_evdev()

    if not vendor_devs:
        print("ERROR: No evdev devices from vendor 1A86 found")
        sys.exit(1)

    print(f"\nVendor evdev devices (1A86:FE00):")
    for dev in vendor_devs:
        caps = dev.capabilities()
        cap_names = []
        for etype in caps:
            name = ecodes.EV.get(etype, f"0x{etype:02x}")
            if name != "EV_SYN":
                cap_names.append(name)
        print(f"  {dev.path}: {dev.name} — PID=0x{dev.info.product:04x} — caps: {', '.join(cap_names)}")

    if xbox_dev:
        print(f"\nXbox gamepad: {xbox_dev.path} ({xbox_dev.name})")
    else:
        print("\nWARNING: Xbox gamepad not found")

    if kb_dev:
        print(f"XFLY keyboard: {kb_dev.path} ({kb_dev.name})")
    else:
        print("WARNING: XFLY keyboard (1A2C:B001) not found")

    # Build list of all devices to monitor
    all_devs = {}
    for dev in vendor_devs:
        all_devs[dev.fd] = (dev, f"VENDOR({os.path.basename(dev.path)})")
    if xbox_dev:
        all_devs[xbox_dev.fd] = (xbox_dev, "XBOX")
    if kb_dev:
        all_devs[kb_dev.fd] = (kb_dev, "XFLY_KB")

    print(f"\nMonitoring {len(all_devs)} devices (NO intercept mode)")
    print("\n" + "-"*60)
    print("INSTRUCTIONS:")
    print("  1. Press and release L4 (left back paddle)")
    print("  2. Press and release R4 (right back paddle)")
    print("  3. Press A, B, Y buttons for comparison")
    print("  4. Press Home / KB buttons")
    print("  5. Move left stick slightly")
    print(f"\nListening for 30 seconds... (Ctrl+C to stop early)")
    print("-"*60 + "\n")

    fds = list(all_devs.keys())
    event_log = []
    deadline = time.monotonic() + 30

    try:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            r, _, _ = select.select(fds, [], [], min(remaining, 0.1))
            for ready_fd in r:
                dev, label = all_devs[ready_fd]
                try:
                    for ev in dev.read():
                        desc = describe_event(ev)
                        if desc:
                            t = time.monotonic()
                            print(f"  [{label:20s}] {desc}")
                            event_log.append((t, label, desc))
                except Exception:
                    pass
    except KeyboardInterrupt:
        print("\n\nStopped.")

    # Cleanup
    for dev, _ in all_devs.values():
        dev.close()

    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)

    if not event_log:
        print("No events received from any device.")
        return

    # Group events by source
    by_source = {}
    for _, label, desc in event_log:
        by_source.setdefault(label, []).append(desc)

    for source, events in sorted(by_source.items()):
        print(f"\n  {source}: {len(events)} events")
        # Show unique event types
        unique = set()
        for e in events:
            unique.add(e.split(" ")[0] + " " + e.split(" ")[1] if " " in e else e)
        for u in sorted(unique):
            count = sum(1 for e in events if e.startswith(u.split(" ")[0] + " " + u.split(" ")[1] if " " in u else u))
            print(f"    {u}: {count}x")

    # Key question
    print("\n" + "-"*60)
    print("KEY QUESTION: Did L4/R4 produce ANY events on vendor evdev")
    print("devices that are DIFFERENT from B/Y on Xbox gamepad?")
    print()
    print("If YES → We can detect paddles without intercept!")
    print("If NO  → Paddles only produce unique events in intercept mode.")
    print("-"*60)


if __name__ == "__main__":
    main()
