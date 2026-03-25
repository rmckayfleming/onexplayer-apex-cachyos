#!/usr/bin/env python3
"""Test direct USB rumble to Xbox pad while in intercept mode.

Bypasses evdev/xpad entirely — writes the Xbox 360 rumble output report
directly to the USB endpoint.

Run as root with HHD stopped:
    sudo systemctl stop hhd@$(whoami)
    sudo python3 scripts/test-rumble-usb-direct.py
    sudo systemctl start hhd@$(whoami)
"""

import glob
import os
import select
import sys
import time

import usb.core
import usb.util


# ---------------------------------------------------------------------------
# Vendor HID helpers (same as other test scripts)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Xbox 360 USB rumble
# ---------------------------------------------------------------------------

def xbox_rumble_report(big_motor, small_motor):
    """Xbox 360 rumble output report — sent to EP 2 OUT."""
    return bytes([0x00, 0x08, 0x00, big_motor, small_motor, 0x00, 0x00, 0x00])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if os.geteuid() != 0:
        print("ERROR: Run as root")
        sys.exit(1)

    # Find vendor HID
    vendor_path = find_vendor_hidraw()
    if not vendor_path:
        print("ERROR: Vendor HID not found")
        sys.exit(1)
    print(f"Vendor HID: {vendor_path}")

    # Find Xbox USB device
    xbox = usb.core.find(idVendor=0x045E, idProduct=0x028E)
    if xbox is None:
        print("ERROR: Xbox pad USB device not found")
        sys.exit(1)
    print(f"Xbox USB:   Bus {xbox.bus:03d} Device {xbox.address:03d}")

    # Detach kernel driver so we can write to the endpoint
    iface = 0
    if xbox.is_kernel_driver_active(iface):
        print(f"Detaching kernel driver from interface {iface}...")
        xbox.detach_kernel_driver(iface)

    # Claim interface
    usb.util.claim_interface(xbox, iface)
    print("USB interface claimed")

    # Find OUT endpoint
    cfg = xbox.get_active_configuration()
    intf = cfg[(0, 0)]
    ep_out = None
    for ep in intf:
        if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT:
            ep_out = ep
            break
    if ep_out is None:
        print("ERROR: No OUT endpoint found")
        sys.exit(1)
    print(f"OUT endpoint: 0x{ep_out.bEndpointAddress:02x}")

    # Open vendor HID
    vfd = os.open(vendor_path, os.O_RDWR)

    # --- Test A: USB rumble WITHOUT intercept (baseline) ---
    print("\n=== TEST A: Direct USB rumble WITHOUT intercept (baseline) ===")
    report = xbox_rumble_report(255, 255)
    print(f"  Sending: {report.hex()}")
    try:
        ep_out.write(report)
        print("  Sent! >>> DO YOU FEEL VIBRATION? <<< (3 seconds)")
        time.sleep(3)
        ep_out.write(xbox_rumble_report(0, 0))
        print("  Stopped")
    except Exception as e:
        print(f"  FAILED: {e}")

    input("\nPress Enter to continue to Test B (with intercept)...")

    # --- Enable intercept ---
    print("\nEnabling intercept mode...")
    os.write(vfd, gen_intercept_enable())
    time.sleep(1)
    alive = check_intercept_alive(vfd)
    print(f"Intercept active: {alive}")
    if not alive:
        print("ABORT: intercept didn't activate")
        os.close(vfd)
        sys.exit(1)
    drain(vfd)

    # --- Test B: USB rumble WITH intercept ---
    print("\n=== TEST B: Direct USB rumble WITH intercept active ===")
    report = xbox_rumble_report(255, 255)
    print(f"  Sending: {report.hex()}")
    try:
        ep_out.write(report)
        print("  Sent!")
    except Exception as e:
        print(f"  FAILED: {e}")

    # Check intercept is still alive
    alive = check_intercept_alive(vfd)
    print(f"  Intercept still active: {alive}")
    print("  >>> DO YOU FEEL VIBRATION? <<< (3 seconds)")
    time.sleep(3)

    try:
        ep_out.write(xbox_rumble_report(0, 0))
        print("  Stopped")
    except Exception as e:
        print(f"  Stop failed: {e}")

    # Verify sticks still work
    print("\n  Move sticks for 2 seconds to verify input...")
    pkt_count = 0
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        r, _, _ = select.select([vfd], [], [], 0.1)
        if vfd in r:
            data = os.read(vfd, 64)
            if len(data) >= 4 and data[0] == 0xB2 and data[3] == 0x02:
                pkt_count += 1
    print(f"  Received {pkt_count} analog packets — {'OK' if pkt_count > 10 else 'LOW'}")

    # Cleanup
    print("\n=== CLEANUP ===")
    os.write(vfd, gen_intercept_disable())
    time.sleep(0.3)
    os.close(vfd)

    usb.util.release_interface(xbox, iface)
    try:
        xbox.attach_kernel_driver(iface)
        print("Kernel driver reattached")
    except Exception:
        print("WARNING: Could not reattach kernel driver — restart HHD")

    print("\n=== RESULTS ===")
    print("  Test A (no intercept):   Did you feel vibration?")
    print("  Test B (with intercept): Did you feel vibration?")
    print("  If B worked + intercept stayed alive = WE HAVE A SOLUTION")


if __name__ == "__main__":
    main()
