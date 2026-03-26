# USB HID Capture Guide — OneXConsole Protocol Reverse Engineering

This guide explains how to capture the exact HID byte commands that OneXConsole sends to the OneXPlayer Apex for button remapping, vibration, and report mode changes. These captures will allow us to implement the same protocol on Linux, enabling back paddles AND rumble simultaneously (without intercept mode).

## Why This Matters

On Linux, we use "intercept mode" (0xB2) to get independent back paddle buttons — but this kills rumble (firmware limitation, exhaustively tested). OneXConsole on Windows uses a **different approach**: it sends remap commands directly to the device firmware, which handles button remapping internally. No intercept mode needed, Xbox gamepad stays alive, rumble works.

The remap protocol is compiled to native code in `CompatLayerCT.exe` and can't be decompiled. USB capture is the fastest way to extract the exact byte sequences.

## What You Need

1. **Windows installation** (your SSD boot)
2. **Wireshark** — https://www.wireshark.org/download.html (install with USBPcap support)
3. **USBPcap** — included in Wireshark installer, tick the checkbox during install
4. **OneXConsole** — https://app.onexconsole.com/web/agg/app:download (or pre-installed on your device)

## Setup (5 minutes)

### Step 1: Install Wireshark with USBPcap

1. Download Wireshark from https://www.wireshark.org/download.html
2. During installation, **check the box for USBPcap** (USB packet capture)
3. Reboot after installation

### Step 2: Identify the HID Device

1. Open Device Manager (Win+X → Device Manager)
2. Find "Human Interface Devices"
3. Look for the OXP vendor device — VID `1A86` PID `FE00` or VID `2563` PID `058D`
4. Note which USB bus it's on (right-click → Properties → Details → Location paths)

## Capture Procedure

### Capture 1: Button Remap Command (MOST IMPORTANT)

This captures the `setKeyMappingInfo` HID command — the key to making paddles work without intercept mode.

1. **Open OneXConsole** and go to the controller/handle settings (the gamepad icon)
2. **Open Wireshark** and start capturing on the USBPcap interface
3. In Wireshark filter bar, type: `usb.transfer_type == 0x00 || usb.transfer_type == 0x02` (control + interrupt transfers)
4. **In OneXConsole**: Change M1 (right back paddle) mapping from "Second Function" to a keyboard key (e.g., "F13" or "Left Ctrl")
5. **Wait 2 seconds** for the command to be sent
6. **Change M1 back** to "Second Function" (default)
7. **Stop the Wireshark capture**
8. Save as `capture-keymapping.pcapng`

### Capture 2: Motor/Vibration Level

1. Start a new Wireshark capture
2. In OneXConsole, go to Settings → Vibration
3. Change the vibration level (e.g., from 3 to 5, then back to 3)
4. If there's a "test vibration" button, press it
5. Stop capture, save as `capture-vibration.pcapng`

### Capture 3: Report Mode Change (Intercept Toggle)

1. Start a new Wireshark capture
2. In OneXConsole, switch between controller profiles or toggle any setting that triggers `changeReportMode`
3. Open and close the OneXConsole quick settings overlay (this triggers report mode changes)
4. Stop capture, save as `capture-reportmode.pcapng`

### Capture 4: Factory Reset (Full Protocol Dump)

1. Start a new Wireshark capture
2. In OneXConsole, go to controller settings
3. Press "Factory Reset" or "Reset to Default"
4. This will send the full default configuration — captures ALL commands at once
5. Stop capture, save as `capture-factoryreset.pcapng`

## Filtering the Captures

After capturing, filter to just the HID commands:

### In Wireshark:

```
usb.src == "host" && usb.data_len > 0
```

This shows only host→device packets (commands we send).

### Look for 64-byte packets

The OXP HID protocol uses 64-byte packets with v1 framing:
```
[CID] [3F] [idx] [payload...] [3F] [CID]
```

Filter for these: `usb.data_len == 64`

### Known Command IDs (CIDs)

| CID | Purpose |
|-----|---------|
| 0xB2 | Intercept mode / report mode |
| 0xB3 | Vibration |
| 0x07 | RGB control |
| 0xF5 | Initialization |

**We're looking for NEW CIDs** — the remap commands will use a CID we haven't seen before.

## What to Look For

### Key Mapping Packets

When you change M1's mapping, you should see one or more 64-byte HID write packets. The packet will contain:
- A CID byte (first byte) — probably something we haven't seen before
- The `3F` framing bytes
- The button code (`22` for M1, `23` for M2)
- The function code (`01`=Xbox, `02`=Keyboard, `05`=SecondFunc)
- The target key/button value

### Vibration Packets

When you change vibration level (0-5), look for a 64-byte packet containing the level value.

### Report Mode Packets

These should match the 0xB2 commands we already know:
- Enable: `B2 3F 01 03 01 02 ...`
- Disable: `B2 3F 01 00 01 02 ...`

## Exporting the Data

### Option A: Copy hex from Wireshark

1. Click on an interesting packet
2. In the bottom pane, right-click the USB data
3. "Copy" → "...as Hex Dump" or "...as Hex Stream"
4. Paste into a text file

### Option B: Export packet bytes

1. File → Export Packet Dissections → As Plain Text
2. Save as `capture-export.txt`

### Option C: Save the raw pcapng

Just save the `.pcapng` files — they can be analyzed later with `tshark` on Linux:
```bash
tshark -r capture-keymapping.pcapng -Y "usb.src == host && usb.data_len == 64" -T fields -e usb.capdata
```

## Quick Reference: What OneXConsole Calls Things

| OneXConsole Name | Our Name | HID Code |
|-----------------|----------|----------|
| M1 | Right back paddle (R4) | 0x22 |
| M2 | Left back paddle (L4) | 0x23 |
| M3 | KB/QAM button | 0x24 |
| HOME | Home/Orange button | 0x21 |
| Second Function | Default M1/M2 mode | funcCode 0x05 |
| XBOX | Remap to Xbox button | funcCode 0x01 |
| Keyboard | Remap to keyboard key | funcCode 0x02 |

## After Capturing

Copy the `.pcapng` files to a USB stick or upload them. With the raw HID packets, we can:

1. Identify the exact CID and packet format for button remapping
2. Implement the same protocol in Python for our HHD patches
3. Remap paddles via firmware commands on Linux — no intercept mode needed
4. Keep Xbox gamepad alive with full rumble support

This would be the definitive fix for the rumble + back paddles problem.

## Alternative: Minimal Capture with USBPcapCMD

If Wireshark is too complex, USBPcap includes a command-line tool:

```cmd
USBPcapCMD.exe -d \\.\USBPcap1 -o capture.pcap -A
```

Then just perform the remap action in OneXConsole and stop the capture with Ctrl+C.
