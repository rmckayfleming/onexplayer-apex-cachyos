#!/usr/bin/env python3
"""Test rumble/vibration approaches while in intercept mode.

Run as root with HHD stopped:
    sudo systemctl stop hhd@$(whoami)
    sudo python3 scripts/test-rumble-intercept.py
    sudo systemctl start hhd@$(whoami)

Tests:
  1. Xbox gamepad FF_RUMBLE while intercepted
  2. Vendor HID 0xB3 with v2 framing (0xFF header)
  3. Vendor HID 0xB3 with v1 framing (0x3F header) — known to break, included for comparison
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
    """Find the OXP vendor HID device (1A86:FE00)."""
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


def find_xbox_evdev():
    """Find the Xbox 360 gamepad evdev device (045E:028E)."""
    try:
        import evdev
    except ImportError:
        print("ERROR: python-evdev not installed")
        return None

    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if dev.info.vendor == 0x045E and dev.info.product == 0x028E:
            return path
        dev.close()
    return None


# ---------------------------------------------------------------------------
# Command generators
# ---------------------------------------------------------------------------

def gen_cmd(cid, cmd, size=64):
    """V2 framing: [cid, 0xFF, *cmd, padding...]"""
    c = bytes(cmd) if isinstance(cmd, list) else cmd
    base = bytes([cid, 0xFF]) + c
    return base + bytes([0] * (size - len(base)))


def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    """V1 framing: [cid, 0x3F, idx, *cmd, padding, 0x3F, cid]"""
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])


def gen_intercept_enable():
    return gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])


def gen_intercept_disable():
    return gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])


def gen_vibration_v1(strength):
    """0xB3 with v1 framing (0x3F) — KNOWN TO BREAK intercept."""
    mode = 0x02 if strength == 0 else 0x01
    cmd = (
        [0x02, 0x38, 0x02, 0xE3, 0x39, 0xE3, 0x39, 0xE3, 0x39,
         mode, strength, strength, 0xE3, 0x39, 0xE3]
        + [0x00] * 35
        + [0x39, 0xE3, 0x39, 0xE3, 0xE3, 0x02, 0x04, 0x39, 0x39]
    )
    return gen_cmd_v1(0xB3, cmd)


def gen_vibration_v2(strong, weak):
    """0xB3 with v2 framing (0xFF) — EXPERIMENT."""
    mode = 0x02 if (strong == 0 and weak == 0) else 0x01
    return gen_cmd(0xB3, [mode, strong, weak])


# ---------------------------------------------------------------------------
# Intercept mode check
# ---------------------------------------------------------------------------

def check_intercept_alive(fd, timeout=1.0):
    """Read from vendor HID to check if intercept packets (0xB2) are still coming.

    In intercept mode, the device continuously sends type 0x02 analog state packets.
    If we get 0xB2 packets, intercept is alive. If nothing or non-0xB2, it's dead.
    """
    got_b2 = False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        r, _, _ = select.select([fd], [], [], min(remaining, 0.1))
        if fd in r:
            data = os.read(fd, 64)
            if len(data) >= 1 and data[0] == 0xB2:
                got_b2 = True
                break
    return got_b2


def drain(fd, duration=0.5):
    """Drain any pending data from fd."""
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        r, _, _ = select.select([fd], [], [], min(remaining, 0.05))
        if fd in r:
            os.read(fd, 64)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_xbox_ff(xbox_path):
    """Test 1: Send FF_RUMBLE to Xbox gamepad while intercept is active."""
    import evdev
    from evdev import ff, ecodes

    print("\n=== TEST 1: Xbox gamepad FF_RUMBLE ===")
    dev = evdev.InputDevice(xbox_path)
    has_ff = ecodes.EV_FF in dev.capabilities()
    print(f"  Device: {dev.path} ({dev.name})")
    print(f"  EV_FF capable: {has_ff}")

    if not has_ff:
        print("  SKIP: No FF support")
        dev.close()
        return False

    try:
        rumble = ff.Rumble(strong_magnitude=0xFFFF, weak_magnitude=0xFFFF)
        effect = ff.Effect(
            ecodes.FF_RUMBLE, -1, 0,
            ff.Trigger(0, 0),
            ff.Replay(2000, 0),
            ff.EffectType(ff_rumble_effect=rumble),
        )
        eid = dev.upload_effect(effect)
        dev.write(ecodes.EV_FF, eid, 1)
        print(f"  FF effect uploaded (id={eid}) and triggered for 2s")
        print("  >>> DO YOU FEEL VIBRATION? (wait 2 seconds) <<<")
        time.sleep(2.5)
        dev.erase_effect(eid)
        print("  FF effect erased")
        dev.close()
        return True
    except Exception as e:
        print(f"  FAILED: {e}")
        dev.close()
        return False


def test_vibration_v2(fd):
    """Test 2: Send 0xB3 with v2 framing (0xFF header)."""
    print("\n=== TEST 2: 0xB3 vibration with v2 framing (0xFF) ===")
    cmd = gen_vibration_v2(255, 255)
    print(f"  Sending: {cmd.hex()}")
    os.write(fd, cmd)
    time.sleep(0.1)

    # Check if intercept is still alive
    alive = check_intercept_alive(fd, timeout=1.5)
    print(f"  Intercept still alive: {alive}")
    if alive:
        print("  >>> DO YOU FEEL VIBRATION? <<<")
        time.sleep(2)
        # Send stop
        os.write(fd, gen_vibration_v2(0, 0))
        print("  Sent vibration stop")
    else:
        print("  BAD: Intercept mode exited! Attempting re-enable...")
        os.write(fd, gen_intercept_enable())
        time.sleep(0.5)
        alive2 = check_intercept_alive(fd, timeout=1.5)
        print(f"  Re-enable result: intercept alive={alive2}")

    return alive


def test_vibration_v1(fd):
    """Test 3: Send 0xB3 with v1 framing (0x3F) — known broken, for comparison."""
    print("\n=== TEST 3: 0xB3 vibration with v1 framing (0x3F) — KNOWN BROKEN ===")
    cmd = gen_vibration_v1(255)
    print(f"  Sending: {cmd.hex()}")
    os.write(fd, cmd)
    time.sleep(0.1)

    alive = check_intercept_alive(fd, timeout=1.5)
    print(f"  Intercept still alive: {alive}")
    if alive:
        print("  SURPRISE: v1 framing didn't break intercept!")
        time.sleep(1)
        os.write(fd, gen_vibration_v1(0))
    else:
        print("  EXPECTED: Intercept mode exited. Re-enabling...")
        os.write(fd, gen_intercept_enable())
        time.sleep(0.5)
        alive2 = check_intercept_alive(fd, timeout=1.5)
        print(f"  Re-enable result: intercept alive={alive2}")

    return alive


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if os.geteuid() != 0:
        print("ERROR: Run as root (sudo)")
        sys.exit(1)

    # Find devices
    vendor_path = find_vendor_hidraw()
    if not vendor_path:
        print("ERROR: Vendor HID device (1A86:FE00) not found")
        sys.exit(1)
    print(f"Vendor HID: {vendor_path}")

    xbox_path = find_xbox_evdev()
    if not xbox_path:
        print("WARNING: Xbox gamepad (045E:028E) not found — skipping FF test")
    else:
        print(f"Xbox pad:   {xbox_path}")

    # Open vendor HID
    fd = os.open(vendor_path, os.O_RDWR)
    print(f"\nOpened vendor HID (fd={fd})")

    # Enable intercept mode
    print("Enabling intercept mode...")
    os.write(fd, gen_intercept_enable())
    time.sleep(1)

    # Verify intercept is active
    alive = check_intercept_alive(fd, timeout=2)
    if not alive:
        print("ERROR: Intercept mode did not activate. Check device.")
        os.close(fd)
        sys.exit(1)
    print("Intercept mode ACTIVE (receiving 0xB2 packets)")

    # Drain buffer
    drain(fd)

    results = {}

    # Test 1: Xbox FF
    if xbox_path:
        try:
            test_xbox_ff(xbox_path)
        except Exception as e:
            print(f"  ERROR: {e}")
        drain(fd)
        input("\nPress Enter to continue to Test 2...")

    # Test 2: 0xB3 v2 framing
    try:
        results["v2"] = test_vibration_v2(fd)
    except Exception as e:
        print(f"  ERROR: {e}")
        results["v2"] = False
    drain(fd)

    input("\nPress Enter to continue to Test 3 (v1 framing — may break intercept)...")

    # Test 3: 0xB3 v1 framing (known broken)
    try:
        results["v1"] = test_vibration_v1(fd)
    except Exception as e:
        print(f"  ERROR: {e}")
        results["v1"] = False

    # Cleanup: disable intercept
    print("\n=== CLEANUP ===")
    print("Disabling intercept mode...")
    os.write(fd, gen_intercept_disable())
    time.sleep(0.3)
    os.close(fd)
    print("Done.")

    # Summary
    print("\n=== RESULTS ===")
    if xbox_path:
        print("  Test 1 (Xbox FF):     Manual — did you feel vibration?")
    print(f"  Test 2 (0xB3 v2):     Intercept survived: {results.get('v2', 'N/A')}")
    print(f"  Test 3 (0xB3 v1):     Intercept survived: {results.get('v1', 'N/A')}")


if __name__ == "__main__":
    main()
