#!/usr/bin/env python3
"""Test back paddles using HIDAPI (same as HHD) instead of raw os.open.

This mimics exactly how HHD opens and communicates with the device.
Run as root: sudo python3 test_hidapi.py
"""
import ctypes
import sys
import time
import select

# Load hidapi like HHD does
for lib in ("libhidapi-hidraw.so", "libhidapi-hidraw.so.0"):
    try:
        hidapi = ctypes.cdll.LoadLibrary(lib)
        print(f"Loaded {lib}")
        break
    except OSError:
        pass
else:
    print("Could not load libhidapi-hidraw.so")
    sys.exit(1)

hidapi.hid_init()

class LinuxHidDevice(ctypes.Structure):
    _fields_ = [
        ("device_handle", ctypes.c_int),
        ("blocking", ctypes.c_int),
        ("last_error_str", ctypes.c_wchar_p),
        ("hid_device_info", ctypes.c_void_p),
    ]

hidapi.hid_open_path.argtypes = [ctypes.c_char_p]
hidapi.hid_open_path.restype = ctypes.POINTER(LinuxHidDevice)
hidapi.hid_write.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
hidapi.hid_write.restype = ctypes.c_int
hidapi.hid_read.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
hidapi.hid_read.restype = ctypes.c_int
hidapi.hid_set_nonblocking.argtypes = [ctypes.c_void_p, ctypes.c_int]
hidapi.hid_set_nonblocking.restype = ctypes.c_int

# Open device like HHD does
path = b"/dev/hidraw5"
dev = hidapi.hid_open_path(path)
if not dev:
    print("Failed to open device")
    sys.exit(1)

fd = dev.contents.device_handle
print(f"Opened {path.decode()} via HIDAPI, fd={fd}, blocking={dev.contents.blocking}")

# Generate v1 intercept command
def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])

INTERCEPT_ON = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
print(f"\nSending intercept ({len(INTERCEPT_ON)} bytes): {INTERCEPT_ON.hex()}")
ret = hidapi.hid_write(dev, INTERCEPT_ON, len(INTERCEPT_ON))
print(f"hid_write returned: {ret}")

time.sleep(0.2)

# Read ACK using select + hid_read (like HHD)
buf = ctypes.create_string_buffer(4096)
print("\nReading ACK with select + hid_read (like HHD)...")
while select.select([fd], [], [], 0.1)[0]:
    size = hidapi.hid_read(dev, buf, 4096)
    if size > 0:
        data = buf.raw[:size]
        print(f"  Read {size} bytes: {data.hex()}")
        if size >= 7:
            print(f"  byte[0]=0x{data[0]:02x} byte[1]=0x{data[1]:02x} byte[6]=0x{data[6]:02x}")

print(f"\nNow press L4/R4 back paddles (Ctrl+C to quit)...")
print(f"Using select() on fd={fd} + hid_read, same as HHD\n")

try:
    while True:
        ready = select.select([fd], [], [], 1.0)[0]
        if not ready:
            continue
        size = hidapi.hid_read(dev, buf, 4096)
        if size <= 0:
            continue
        data = buf.raw[:size]

        if data[0] == 0xB2 and size >= 13:
            btn = data[6]
            state = data[12]
            valid = data[1] == 0x3F and (size >= 63 and data[-2] == 0x3F)

            pkt_type = data[3]
            if pkt_type == 0x01:
                btn_name = {0x22: "L4", 0x23: "R4"}.get(btn, f"0x{btn:02x}")
                state_name = {0x01: "PRESS", 0x02: "RELEASE"}.get(state, f"0x{state:02x}")
                print(f"** BUTTON: {btn_name} {state_name} ** valid={valid} size={size}")
            else:
                type_name = {0x03: "ACK", 0x02: "GAMEPAD"}.get(pkt_type, f"0x{pkt_type:02x}")
                print(f"   {type_name}: btn=0x{btn:02x} size={size} valid={valid}")
        else:
            print(f"   other[{size}]: {data[:16].hex()}...")

except KeyboardInterrupt:
    print("\n\nSending intercept OFF...")
    INTERCEPT_OFF = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])
    hidapi.hid_write(dev, INTERCEPT_OFF, len(INTERCEPT_OFF))
    hidapi.hid_close(dev)
    print("Done.")
