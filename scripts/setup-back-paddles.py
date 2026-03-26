#!/usr/bin/env python3
"""Set up back paddles (M1/M2) via firmware remap.

Usage: sudo python3 scripts/setup-back-paddles.py

Sends firmware commands to remap M1→F14, M2→F13 and activates
report mode so the controller emits paddle events.
"""

import os
import sys

# Add py_modules to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "decky-plugin", "py_modules"))

import back_paddle

# Use print for logging
back_paddle.set_log_callbacks(
    lambda msg: print(f"  [INFO]  {msg}"),
    lambda msg: print(f"  [ERROR] {msg}"),
    lambda msg: print(f"  [WARN]  {msg}"),
)

print("Setting up back paddles...")
print()

result = back_paddle.setup_paddles()

print()
if result.get("success"):
    print("SUCCESS — Back paddles configured!")
    print("  M1 (right paddle) → F14 → RightPaddle1")
    print("  M2 (left paddle)  → F13 → LeftPaddle1")
    print()
    print("Now test by pressing the back paddles while running:")
    print("  sudo python3 scripts/monitor-button-events.py")
else:
    print(f"FAILED — {result.get('error', 'unknown error')}")
    sys.exit(1)
