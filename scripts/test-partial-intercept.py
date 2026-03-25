#!/usr/bin/env python3
"""Test partial intercept modes by varying 0xB2 command parameters.

The full intercept command is gen_cmd_v1(0xB2, [0x03, 0x01, 0x02]).
We don't know what each byte means. This script tries different
combinations to find a "buttons-only" intercept that keeps the Xbox
gamepad active (so FF_RUMBLE still works) while capturing L4/R4.

Run as root with HHD stopped:
    sudo systemctl stop hhd@$(whoami)
    sudo python3 scripts/test-partial-intercept.py
    sudo systemctl start hhd@$(whoami)

For each parameter set, the script will:
  1. Send the 0xB2 command with those parameters
  2. Listen for vendor HID packets (button events / analog state)
  3. Check if Xbox gamepad evdev still produces events
  4. Ask you to press L4/R4 and report what happens
  5. Clean up by sending disable command
"""

import glob
import os
import select
import sys
import time

try:
    import evdev
    from evdev import ecodes
    HAS_EVDEV = True
except ImportError:
    HAS_EVDEV = False
    print("WARNING: python-evdev not installed — Xbox gamepad checks will be skipped")


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


def find_xbox_evdev():
    """Find the Xbox 360 gamepad evdev device (045E:028E)."""
    if not HAS_EVDEV:
        return None
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if dev.info.vendor == 0x045E and dev.info.product == 0x028E:
            dev.close()
            return path
        dev.close()
    return None


# ---------------------------------------------------------------------------
# Command generators
# ---------------------------------------------------------------------------

def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    """V1 framing: [cid, 0x3F, idx, *cmd, padding, 0x3F, cid]"""
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])


def gen_intercept_disable():
    return gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def drain(fd, duration=0.3):
    """Drain pending data from vendor HID fd."""
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        r, _, _ = select.select([fd], [], [], 0.05)
        if fd in r:
            os.read(fd, 64)


def read_vendor_packets(fd, duration=2.0):
    """Read vendor HID packets for a duration. Returns list of (timestamp, raw_bytes)."""
    packets = []
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        r, _, _ = select.select([fd], [], [], min(remaining, 0.05))
        if fd in r:
            data = os.read(fd, 64)
            packets.append((time.monotonic(), data))
    return packets


def check_xbox_alive(xbox_path, duration=2.0):
    """Check if Xbox gamepad produces any evdev events (move sticks to test)."""
    if not HAS_EVDEV or not xbox_path:
        return None  # Can't check
    try:
        dev = evdev.InputDevice(xbox_path)
        events = []
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            r, _, _ = select.select([dev.fd], [], [], min(remaining, 0.05))
            if dev.fd in r:
                for ev in dev.read():
                    if ev.type in (ecodes.EV_ABS, ecodes.EV_KEY):
                        events.append(ev)
        dev.close()
        return len(events) > 0
    except Exception as e:
        print(f"    Xbox check error: {e}")
        return None


def classify_vendor_packets(packets):
    """Classify vendor HID packets by type."""
    types = {"button": 0, "analog": 0, "ack": 0, "other": 0}
    for _, data in packets:
        if len(data) < 4:
            types["other"] += 1
            continue
        if data[0] == 0xB2:
            pkt_type = data[3]
            if pkt_type == 0x01:
                types["button"] += 1
            elif pkt_type == 0x02:
                types["analog"] += 1
            elif pkt_type == 0x03:
                types["ack"] += 1
            else:
                types["other"] += 1
        else:
            types["other"] += 1
    return types


# ---------------------------------------------------------------------------
# Parameter sets to test
# ---------------------------------------------------------------------------

PARAM_SETS = [
    # (description, [byte0, byte1, byte2])
    ("Full intercept (baseline)",       [0x03, 0x01, 0x02]),
    ("Byte0=0x01 (buttons only?)",      [0x01, 0x01, 0x02]),
    ("Byte0=0x02 (buttons+triggers?)",  [0x02, 0x01, 0x02]),
    ("Byte2=0x01",                      [0x03, 0x01, 0x01]),
    ("Byte2=0x00",                      [0x03, 0x01, 0x00]),
    ("Byte1=0x00",                      [0x03, 0x00, 0x02]),
    ("Byte1=0x02",                      [0x03, 0x02, 0x02]),
    ("Minimal (0x01,0x00,0x00)",        [0x01, 0x00, 0x00]),
    ("Byte0=0x04",                      [0x04, 0x01, 0x02]),
    ("Byte0=0x05",                      [0x05, 0x01, 0x02]),
    ("Byte0=0x06",                      [0x06, 0x01, 0x02]),
    ("Byte0=0x07",                      [0x07, 0x01, 0x02]),
]


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------

def test_params(fd, xbox_path, desc, params):
    """Test a single parameter set."""
    print(f"\n{'='*60}")
    print(f"  {desc}")
    print(f"  Params: [{', '.join(f'0x{b:02x}' for b in params)}]")
    print(f"{'='*60}")

    # Send intercept command with these params
    cmd = gen_cmd_v1(0xB2, params)
    print(f"  Sending: {cmd[:8].hex()}...{cmd[-4:].hex()}")
    os.write(fd, cmd)
    time.sleep(0.5)

    # Phase 1: Check for vendor HID packets (passive, just wait)
    print("\n  [Phase 1] Listening for vendor HID packets (2s, move sticks + press buttons)...")
    packets = read_vendor_packets(fd, duration=2.0)
    types = classify_vendor_packets(packets)
    print(f"    Total packets: {len(packets)}")
    print(f"    Button events: {types['button']}, Analog state: {types['analog']}, "
          f"ACK: {types['ack']}, Other: {types['other']}")

    if packets:
        first = packets[0][1]
        print(f"    First packet: {first[:16].hex()}...")

    # Phase 2: Check if Xbox gamepad is still alive
    if xbox_path:
        print("\n  [Phase 2] Checking Xbox gamepad (2s, MOVE THE STICKS!)...")
        xbox_alive = check_xbox_alive(xbox_path, duration=2.0)
        if xbox_alive is None:
            print("    Could not check Xbox gamepad")
        elif xbox_alive:
            print("    Xbox gamepad: ALIVE (producing events)")
        else:
            print("    Xbox gamepad: SILENT (no events — fully intercepted)")
    else:
        print("\n  [Phase 2] Xbox gamepad not found — skipping")
        xbox_alive = None

    # Phase 3: Check for L4/R4 button codes
    print("\n  [Phase 3] Press L4 and R4 back paddles NOW (3s)...")
    paddle_packets = read_vendor_packets(fd, duration=3.0)
    paddle_types = classify_vendor_packets(paddle_packets)
    l4_found = False
    r4_found = False
    for _, data in paddle_packets:
        if len(data) >= 13 and data[0] == 0xB2 and data[3] == 0x01:
            btn_code = data[6]
            if btn_code == 0x22:
                r4_found = True
                print(f"    >>> R4 (0x22) detected! pressed={data[12]}")
            elif btn_code == 0x23:
                l4_found = True
                print(f"    >>> L4 (0x23) detected! pressed={data[12]}")
            else:
                print(f"    Button 0x{btn_code:02x} pressed={data[12]}")

    print(f"    L4 detected: {l4_found}, R4 detected: {r4_found}")
    print(f"    Total paddle-phase packets: {len(paddle_packets)} "
          f"(btn={paddle_types['button']}, analog={paddle_types['analog']})")

    # Disable intercept
    print("\n  Disabling intercept...")
    os.write(fd, gen_intercept_disable())
    time.sleep(0.5)
    drain(fd)

    # Summary for this test
    result = {
        "desc": desc,
        "params": params,
        "vendor_packets": len(packets),
        "analog_packets": types["analog"],
        "button_packets": types["button"],
        "xbox_alive": xbox_alive,
        "l4_found": l4_found,
        "r4_found": r4_found,
    }

    # Flag interesting results
    interesting = False
    if (l4_found or r4_found) and xbox_alive:
        print("\n  *** INTERESTING: Paddles detected AND Xbox gamepad alive! ***")
        interesting = True
    elif xbox_alive and types["analog"] == 0:
        print("\n  * NOTE: Xbox alive + no analog intercept — partial mode? Press paddles again to check.")
        interesting = True

    result["interesting"] = interesting
    return result


def main():
    if os.geteuid() != 0:
        print("ERROR: Run as root (sudo)")
        sys.exit(1)

    vendor_path = find_vendor_hidraw()
    if not vendor_path:
        print("ERROR: Vendor HID device (1A86:FE00) not found")
        sys.exit(1)
    print(f"Vendor HID: {vendor_path}")

    xbox_path = find_xbox_evdev()
    if xbox_path:
        print(f"Xbox pad:   {xbox_path}")
    else:
        print("WARNING: Xbox gamepad not found — will skip Xbox checks")

    fd = os.open(vendor_path, os.O_RDWR)
    print(f"Opened vendor HID (fd={fd})")

    print("\n" + "="*60)
    print("PARTIAL INTERCEPT PARAMETER EXPLORATION")
    print("="*60)
    print(f"\nWill test {len(PARAM_SETS)} parameter combinations.")
    print("For each test:")
    print("  - Move sticks and press face buttons during Phase 1+2")
    print("  - Press L4/R4 back paddles during Phase 3")
    print("\nPress Enter to start...")
    input()

    results = []
    for desc, params in PARAM_SETS:
        result = test_params(fd, xbox_path, desc, params)
        results.append(result)

        # Pause between tests
        print("\n  Press Enter for next test (or Ctrl+C to stop)...")
        try:
            input()
        except KeyboardInterrupt:
            print("\n\nStopping early.")
            break

    os.close(fd)

    # Final summary
    print("\n" + "="*60)
    print("FINAL SUMMARY")
    print("="*60)
    print(f"\n{'Description':<40} {'Vendor':<8} {'Analog':<8} {'Xbox':<8} {'L4':<5} {'R4':<5} {'!!!'}")
    print("-" * 90)
    for r in results:
        xbox_str = "alive" if r["xbox_alive"] else ("silent" if r["xbox_alive"] is False else "?")
        flag = " <<<" if r["interesting"] else ""
        print(f"{r['desc']:<40} {r['vendor_packets']:<8} {r['analog_packets']:<8} "
              f"{xbox_str:<8} {str(r['l4_found']):<5} {str(r['r4_found']):<5}{flag}")

    # Highlight winning combinations
    winners = [r for r in results if r["interesting"]]
    if winners:
        print(f"\n*** {len(winners)} INTERESTING RESULT(S) FOUND ***")
        for w in winners:
            params_str = ', '.join(f'0x{b:02x}' for b in w['params'])
            print(f"  [{params_str}] — {w['desc']}")
        print("\nNext step: test 0xB3 rumble with these params to see if it works without exiting the mode!")
    else:
        print("\nNo partial intercept mode found. All parameter sets either:")
        print("  - Fully intercept (Xbox silent, paddles work)")
        print("  - Do nothing (no vendor packets, Xbox alive, no paddles)")


if __name__ == "__main__":
    main()
