#!/bin/bash
# Watch Xbox gamepad + HID keyboard for paddle presses.
# Run as root: sudo bash scripts/watch-paddles.sh
# Press back paddles, then Ctrl+C to stop.

echo "=== Paddle Watcher ==="
echo "Press back paddles now. Ctrl+C to stop."
echo ""

for dev in /dev/input/event*; do
    name=$(cat "/sys/class/input/$(basename "$dev")/device/name" 2>/dev/null)
    case "$name" in
        *X-Box*|*Xbox*|"HID 1a86:fe00")
            echo "Watching: $dev ($name)"
            evtest "$dev" 2>/dev/null | grep --line-buffered "^Event:" | while read line; do echo "[$name] $line"; done &
            ;;
    esac
done

wait
