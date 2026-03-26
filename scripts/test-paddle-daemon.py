#!/usr/bin/env python3
"""Test the paddle daemon — reads B2 hidraw packets, injects F13/F14 via uinput.

Usage: sudo python3 scripts/test-paddle-daemon.py

First runs back paddle setup (firmware remap + B2 activation),
then starts the uinput bridge daemon. Press paddles to see F13/F14
injected as keyboard events.

Ctrl+C to stop.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "decky-plugin", "py_modules"))

import back_paddle
import paddle_daemon

def log_info(msg):
    print(f"  [INFO]  {msg}")
def log_error(msg):
    print(f"  [ERROR] {msg}")
def log_warn(msg):
    print(f"  [WARN]  {msg}")

back_paddle.set_log_callbacks(log_info, log_error, log_warn)
paddle_daemon.set_log_callbacks(log_info, log_error, log_warn)

async def main():
    print("=== Step 1: Firmware remap + B2 activation ===")
    result = back_paddle.setup_paddles()
    if result.get("success"):
        print("  Setup OK")
    else:
        print(f"  Setup FAILED: {result.get('error')}")
        return

    print()
    print("=== Step 2: Starting uinput bridge daemon ===")
    daemon = paddle_daemon.PaddleDaemon()
    loop = asyncio.get_event_loop()
    daemon.start(loop)

    print()
    print("  Paddle daemon running. Press back paddles!")
    print("  You should see F13/F14 key injections.")
    print("  Ctrl+C to stop.")
    print()

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print()
        print("Stopping...")
        await daemon.stop()

asyncio.run(main())
