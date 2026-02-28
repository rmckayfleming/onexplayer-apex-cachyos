# OneXPlayer Apex Fan Control — Implementation Plan

## Problem Statement

The OneXPlayer OneXFly Apex running Bazzite (Fedora-based SteamOS) has no working fan control in Game Mode. Bazzite is transitioning from Handheld Daemon (HHD) to InputPlumber, so the HHD fan control path is unstable. A standalone CLI + Decky Loader plugin is needed as a temporary solution.

---

## 1. Hardware Background

### Device Specs
- **APU**: AMD Ryzen AI Max+ 395 (Strix Halo), Zen 5, up to 16 cores
- **GPU**: Radeon 8060S iGPU (40 CUs)
- **Cooling**: Dual-fan system, 4 copper heat pipes, fans capable of 5400 RPM
- **EC Interface**: ACPI Embedded Controller accessed via I/O ports `0x62`/`0x66`

### EC Register Map (OneXFly/Apex Line)

| Register | Address | R/W | Description |
|----------|---------|-----|-------------|
| `FAN_REG` | `0x76` | R | Fan RPM (2 bytes, little-endian) |
| `PWM_ENABLE` | `0x4A` | R/W | `0x00` = auto (EC controls fan), `0x01` = manual |
| `PWM_VALUE` | `0x4B` | R/W | Fan duty cycle, native range **0–184** |
| `TURBO_SWITCH` | `0xF1` | R/W | Turbo button takeover (write `0x40` to capture) |

The sysfs hwmon driver (`oxp-sensors` / `oxpec`) scales the native 0–184 PWM range to the standard 0–255 range for userspace.

### Kernel Driver Status

The `oxpec` driver (renamed from `oxp-sensors`) has an upstream patch adding Apex support submitted **Feb 23, 2026** (v2 patch series by Antheas Kapenekakis). This means:

- **If your kernel has the patch** (likely Linux 6.16+ or Bazzite with the backport): fan control works via `/sys/class/hwmon/hwmonX/` sysfs interface out of the box.
- **If your kernel does NOT have the patch**: you'll need the [oxp-platform-dkms](https://github.com/Samsagax/oxp-platform-dkms) module with Apex DMI entries added manually, **or** direct EC register access via `ec_sys`.

### HHD → InputPlumber Migration (Does It Affect the Kernel Patches?)

**No.** The kernel driver (`oxpec`) and the userspace input/fan stack (HHD or InputPlumber) are completely separate layers:

| Layer | Old Stack | New Stack | Maintained By |
|-------|-----------|-----------|---------------|
| **Input handling** (userspace) | HHD (Python) | InputPlumber (Rust, ShadowBlip) | OGC community |
| **Kernel driver** (`oxpec`) | Upstream Linux | Upstream Linux (unchanged) | Antheas Kapenekakis |
| **Fan/TDP/RGB UI** | HHD overlay | Steam UI + InputPlumber overlay | OGC / Valve |

**Background**: Antheas was removed from the Bazzite project in late 2025 (CoC violations). The **Open Gaming Collective (OGC)** was announced Jan 29, 2026. Bazzite is dropping HHD in favor of InputPlumber and switching from `kernel-bazzite` to a shared OGC Kernel. However:

- Antheas still maintains `oxpec` upstream independently — the Apex patches are submitted to `platform-driver-x86@vger.kernel.org` and will land in mainline Linux (likely 6.16+) regardless of what Bazzite does with its userspace stack.
- Antheas still maintains HHD independently ([hhd-dev/hhd](https://github.com/hhd-dev/hhd) had a release as recently as Feb 7, 2026).
- The fan control CLI and Decky plugin in this plan do **not** depend on HHD or InputPlumber — they talk directly to the kernel hwmon sysfs or EC registers.

### Manually Bringing oxpec Apex Patches into Bazzite

Bazzite is an immutable OS (OSTree-based), so **DKMS does not work**. Here are your options, from easiest to most robust:

#### Option A: Quick Test with `insmod` (Non-Persistent)
```bash
# Build in a distrobox/toolbox container with matching kernel-devel
distrobox enter
# ... clone oxpec source, build the .ko inside the container ...
exit

# Load on the host
sudo insmod /path/to/oxpec.ko
# Lost on reboot
```

#### Option B: `ostree admin unlock --hotfix` (Persists Until Next Update)
```bash
sudo ostree admin unlock --hotfix
sudo cp oxpec.ko /usr/lib/modules/$(uname -r)/extra/
sudo depmod -a
sudo modprobe oxpec
# Survives reboots but lost on next Bazzite image update
```

#### Option C: Persistent via systemd (No Unlock Needed — Recommended)
```bash
# Put the .ko somewhere writable
sudo mkdir -p /var/lib/custom-modules
sudo cp oxpec.ko /var/lib/custom-modules/

# Create a systemd service to load it at boot
sudo tee /etc/systemd/system/load-oxpec.service <<'EOF'
[Unit]
Description=Load oxpec kernel module
After=systemd-modules-load.service

[Service]
Type=oneshot
ExecStart=/usr/sbin/insmod /var/lib/custom-modules/oxpec.ko
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable load-oxpec.service
```

#### Option D: Custom Bazzite Image with BlueBuild (Production-Grade)
Build your own Bazzite image with the kmod baked in using a Containerfile. This survives all updates because you control the image. See [ublue-os/image-template](https://github.com/ublue-os/image-template).

**Note**: The main challenge for Options A-C is getting matching `kernel-devel` headers for Bazzite's custom kernel. The [bazzite-org/kernel-bazzite](https://github.com/bazzite-org/kernel-bazzite) repo produces `kernel-devel` RPMs, but version mismatches are common.

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────┐
│                    Steam Game Mode                    │
│  ┌─────────────────────────────────────────────────┐  │
│  │          Decky Loader (QAM sidebar)             │  │
│  │  ┌───────────────────────────────────────────┐  │  │
│  │  │   oxp-fan-control (Decky Plugin)          │  │  │
│  │  │                                           │  │  │
│  │  │  Frontend (React/TypeScript)              │  │  │
│  │  │  - Manual/Auto toggle                     │  │  │
│  │  │  - Fan speed slider (0–100%)              │  │  │
│  │  │  - Fan curve editor (temp → speed)        │  │  │
│  │  │  - Current RPM + temp display             │  │  │
│  │  │                                           │  │  │
│  │  │  Backend (Python, runs as root)           │  │  │
│  │  │  - Reads/writes hwmon sysfs               │  │  │
│  │  │  - Fallback: direct EC via ec_sys         │  │  │
│  │  │  - Fan curve loop (async)                 │  │  │
│  │  │  - Settings persistence (JSON)            │  │  │
│  │  └───────────────────────────────────────────┘  │  │
│  └─────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────┐
│  CLI tool: oxp-fan-ctl                               │
│  (standalone bash/python script for terminal use)    │
│  - oxp-fan-ctl auto                                  │
│  - oxp-fan-ctl set 60                                │
│  - oxp-fan-ctl status                                │
│  - oxp-fan-ctl curve                                 │
└──────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────┐
│  Kernel: oxpec hwmon driver (or ec_sys fallback)     │
│  /sys/class/hwmon/hwmonX/pwm1                        │
│  /sys/class/hwmon/hwmonX/pwm1_enable                 │
│  /sys/class/hwmon/hwmonX/fan1_input                  │
└──────────────────────────────────────────────────────┘
```

---

## 3. Phase 1 — CLI Tool (`oxp-fan-ctl`)

Build the CLI first. This validates that fan control works on your hardware and gives you an immediate, testable solution you can use from the terminal or Desktop Mode.

### 3a. Detect the hwmon Device

The CLI needs to find the correct hwmon device. Scan `/sys/class/hwmon/` for the `oxpec` (or `oxp_ec`) driver:

```python
#!/usr/bin/env python3
"""oxp-fan-ctl: OneXPlayer Apex fan control CLI"""

import os
import sys
import time
import json
import glob
import argparse

HWMON_BASE = "/sys/class/hwmon"
DRIVER_NAMES = ["oxpec", "oxp_ec", "oxp-sensors"]

# EC fallback paths
EC_IO = "/sys/kernel/debug/ec/ec0/io"
EC_PWM_ENABLE = 0x4A
EC_PWM_VALUE = 0x4B
EC_FAN_RPM = 0x76

# PWM constants
PWM_MAX = 255  # sysfs standard range
NATIVE_PWM_MAX = 184  # OneXFly EC native range (only needed for direct EC)


def find_hwmon():
    """Find the oxpec hwmon device path."""
    for hwmon in sorted(glob.glob(f"{HWMON_BASE}/hwmon*")):
        name_file = os.path.join(hwmon, "name")
        if os.path.exists(name_file):
            with open(name_file) as f:
                name = f.read().strip()
            if name in DRIVER_NAMES:
                return hwmon
    return None


def find_temp_sensor():
    """Find the best CPU/APU temperature sensor."""
    for hwmon in sorted(glob.glob(f"{HWMON_BASE}/hwmon*")):
        name_file = os.path.join(hwmon, "name")
        if os.path.exists(name_file):
            with open(name_file) as f:
                name = f.read().strip()
            # k10temp is the AMD CPU temp driver
            if name in ["k10temp", "zenpower"]:
                temp_file = os.path.join(hwmon, "temp1_input")
                if os.path.exists(temp_file):
                    return temp_file
    # Fallback: first temp sensor found
    for hwmon in sorted(glob.glob(f"{HWMON_BASE}/hwmon*")):
        for temp in sorted(glob.glob(os.path.join(hwmon, "temp*_input"))):
            return temp
    return None
```

### 3b. Core Operations via hwmon sysfs

```python
class HwmonFanController:
    """Fan control via the oxpec hwmon sysfs interface."""

    def __init__(self, hwmon_path):
        self.pwm = os.path.join(hwmon_path, "pwm1")
        self.pwm_enable = os.path.join(hwmon_path, "pwm1_enable")
        self.fan_input = os.path.join(hwmon_path, "fan1_input")

    def get_rpm(self):
        with open(self.fan_input) as f:
            return int(f.read().strip())

    def get_pwm(self):
        with open(self.pwm) as f:
            return int(f.read().strip())

    def get_mode(self):
        with open(self.pwm_enable) as f:
            val = int(f.read().strip())
        # New ABI: 0=full, 1=manual, 2=auto
        return {0: "full", 1: "manual", 2: "auto"}.get(val, "unknown")

    def set_auto(self):
        with open(self.pwm_enable, "w") as f:
            f.write("2")

    def set_manual(self, percent):
        """Set manual fan speed as a percentage (0-100)."""
        pwm_value = int(percent / 100 * PWM_MAX)
        pwm_value = max(0, min(PWM_MAX, pwm_value))
        with open(self.pwm_enable, "w") as f:
            f.write("1")
        with open(self.pwm, "w") as f:
            f.write(str(pwm_value))
```

### 3c. Direct EC Fallback (if kernel driver is missing)

If the `oxpec` driver isn't loaded or doesn't support the Apex yet, fall back to direct EC register access:

```python
class ECFanController:
    """Fan control via direct EC register access (ec_sys module)."""

    def __init__(self):
        if not os.path.exists(EC_IO):
            # Try loading ec_sys with write support
            os.system("modprobe ec_sys write_support=1")
        if not os.path.exists(EC_IO):
            raise RuntimeError("Cannot access EC: ec_sys module not available")

    def _read_byte(self, offset):
        with open(EC_IO, "rb") as f:
            f.seek(offset)
            return f.read(1)[0]

    def _write_byte(self, offset, value):
        with open(EC_IO, "r+b") as f:
            f.seek(offset)
            f.write(bytes([value]))

    def _read_word(self, offset):
        with open(EC_IO, "rb") as f:
            f.seek(offset)
            data = f.read(2)
        return int.from_bytes(data, "little")

    def get_rpm(self):
        return self._read_word(EC_FAN_RPM)

    def get_pwm(self):
        raw = self._read_byte(EC_PWM_VALUE)
        return int(raw / NATIVE_PWM_MAX * 100)  # return as percentage

    def get_mode(self):
        val = self._read_byte(EC_PWM_ENABLE)
        return "manual" if val == 0x01 else "auto"

    def set_auto(self):
        self._write_byte(EC_PWM_ENABLE, 0x00)

    def set_manual(self, percent):
        """Set fan speed as percentage (0-100)."""
        native_val = int(percent / 100 * NATIVE_PWM_MAX)
        native_val = max(0, min(NATIVE_PWM_MAX, native_val))
        self._write_byte(EC_PWM_ENABLE, 0x01)
        self._write_byte(EC_PWM_VALUE, native_val)
```

### 3d. Fan Curve Engine

```python
DEFAULT_CURVE = [
    # (temp_celsius, fan_percent)
    (40, 0),
    (50, 20),
    (60, 40),
    (70, 60),
    (80, 80),
    (90, 100),
]

class FanCurveController:
    """Runs a fan curve loop, adjusting speed based on temperature."""

    def __init__(self, fan_ctrl, temp_sensor_path, curve=None,
                 interval=2.0, hysteresis=2):
        self.fan = fan_ctrl
        self.temp_path = temp_sensor_path
        self.curve = curve or DEFAULT_CURVE
        self.interval = interval
        self.hysteresis = hysteresis
        self._running = False

    def get_temp(self):
        with open(self.temp_path) as f:
            return int(f.read().strip()) / 1000  # millidegrees to degrees

    def interpolate(self, temp):
        """Linear interpolation on the fan curve."""
        if temp <= self.curve[0][0]:
            return self.curve[0][1]
        if temp >= self.curve[-1][0]:
            return self.curve[-1][1]
        for i in range(len(self.curve) - 1):
            t0, s0 = self.curve[i]
            t1, s1 = self.curve[i + 1]
            if t0 <= temp <= t1:
                ratio = (temp - t0) / (t1 - t0)
                return s0 + ratio * (s1 - s0)
        return self.curve[-1][1]

    def run(self):
        """Main fan curve loop. Blocks until interrupted."""
        self._running = True
        last_speed = -1
        print(f"Fan curve active. Interval: {self.interval}s. Ctrl+C to stop.")
        try:
            while self._running:
                temp = self.get_temp()
                target = self.interpolate(temp)
                # Apply hysteresis: only change if delta > hysteresis%
                if abs(target - last_speed) > self.hysteresis or last_speed < 0:
                    self.fan.set_manual(target)
                    last_speed = target
                    print(f"  Temp: {temp:.1f}°C → Fan: {target:.0f}%")
                time.sleep(self.interval)
        except KeyboardInterrupt:
            print("\nRestoring auto fan control...")
            self.fan.set_auto()
```

### 3e. CLI Interface

```python
def main():
    parser = argparse.ArgumentParser(
        description="OneXPlayer Apex fan control")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show current fan status")
    set_cmd = sub.add_parser("set", help="Set fan speed (0-100%%)")
    set_cmd.add_argument("percent", type=int)
    sub.add_parser("auto", help="Return to automatic fan control")
    sub.add_parser("max", help="Set fans to maximum speed")
    curve_cmd = sub.add_parser("curve", help="Run fan curve daemon")
    curve_cmd.add_argument("--config", help="JSON fan curve config file")

    args = parser.parse_args()

    # Auto-detect controller
    hwmon = find_hwmon()
    if hwmon:
        fan = HwmonFanController(hwmon)
        print(f"Using hwmon driver at {hwmon}")
    else:
        fan = ECFanController()
        print("Using direct EC access (no hwmon driver found)")

    if args.command == "status":
        print(f"Mode: {fan.get_mode()}")
        print(f"RPM:  {fan.get_rpm()}")
        if hasattr(fan, 'pwm'):
            pwm = fan.get_pwm()
            print(f"PWM:  {pwm}/255 ({pwm/255*100:.0f}%)")

    elif args.command == "set":
        fan.set_manual(args.percent)
        print(f"Fan set to {args.percent}%")

    elif args.command == "auto":
        fan.set_auto()
        print("Fan control returned to automatic")

    elif args.command == "max":
        fan.set_manual(100)
        print("Fans set to maximum")

    elif args.command == "curve":
        temp_sensor = find_temp_sensor()
        if not temp_sensor:
            print("ERROR: No temperature sensor found", file=sys.stderr)
            sys.exit(1)
        curve = DEFAULT_CURVE
        if args.config:
            with open(args.config) as f:
                curve = json.load(f)
        ctrl = FanCurveController(fan, temp_sensor, curve)
        ctrl.run()

    else:
        parser.print_help()

if __name__ == "__main__":
    main()
```

### 3f. Usage

```bash
# Check status
sudo oxp-fan-ctl status

# Set to 60%
sudo oxp-fan-ctl set 60

# Back to automatic
sudo oxp-fan-ctl auto

# Full blast
sudo oxp-fan-ctl max

# Run a fan curve (blocks, Ctrl+C to stop)
sudo oxp-fan-ctl curve

# Custom fan curve from JSON file
sudo oxp-fan-ctl curve --config my_curve.json
```

Example `my_curve.json`:
```json
[[35, 0], [45, 15], [55, 35], [65, 55], [75, 75], [85, 100]]
```

### 3g. Systemd Service (Optional, for Headless Use)

```ini
# /etc/systemd/system/oxp-fan-curve.service
[Unit]
Description=OneXPlayer Apex Fan Curve Controller
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/local/bin/oxp-fan-ctl curve
ExecStopPost=/usr/local/bin/oxp-fan-ctl auto
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## 4. Phase 2 — Decky Loader Plugin

Once the CLI works, wrap it in a Decky plugin for Game Mode control.

### 4a. Project Setup

```bash
# Use the official template
git clone https://github.com/SteamDeckHomebrew/decky-plugin-template oxp-fan-control
cd oxp-fan-control
pnpm install
```

### 4b. File Structure

```
oxp-fan-control/
├── src/
│   └── index.tsx              # React frontend (QAM panel UI)
├── py_modules/
│   └── fan_control.py         # Reusable fan control module (from CLI)
├── main.py                    # Decky Python backend
├── plugin.json                # Plugin metadata
├── package.json               # npm config
├── defaults/                  # Default config files
│   └── fan_curve.json
├── tsconfig.json
└── rollup.config.js
```

### 4c. `plugin.json`

```json
{
  "name": "OXP Fan Control",
  "author": "Your Name",
  "flags": ["root"],
  "publish": {
    "tags": ["hardware", "fan-control", "onexplayer"],
    "description": "Fan speed control for OneXPlayer Apex handhelds"
  }
}
```

The `"root"` flag is critical — the Decky backend runs as a root systemd service, and this flag ensures your plugin inherits those permissions for sysfs/EC writes.

### 4d. Python Backend (`main.py`)

```python
import os
import json
import asyncio
import decky

# Import the shared fan control module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "py_modules"))
from fan_control import (
    find_hwmon, find_temp_sensor,
    HwmonFanController, ECFanController, FanCurveController,
    DEFAULT_CURVE
)


class Plugin:
    fan = None
    curve_ctrl = None
    curve_task = None
    settings_path = None

    async def _main(self):
        """Plugin initialization."""
        self.settings_path = os.path.join(
            decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")
        self._init_fan()
        settings = self._load_settings()
        # If fan curve was active when plugin last ran, restart it
        if settings.get("mode") == "curve":
            await self.start_curve(
                settings.get("curve", DEFAULT_CURVE),
                settings.get("interval", 2.0)
            )

    async def _unload(self):
        """Plugin shutdown — always restore auto."""
        await self.stop_curve()
        if self.fan:
            self.fan.set_auto()

    def _init_fan(self):
        hwmon = find_hwmon()
        if hwmon:
            self.fan = HwmonFanController(hwmon)
            decky.logger.info(f"Using hwmon at {hwmon}")
        else:
            try:
                self.fan = ECFanController()
                decky.logger.info("Using direct EC access")
            except RuntimeError as e:
                decky.logger.error(f"No fan control available: {e}")

    def _load_settings(self):
        if os.path.exists(self.settings_path):
            with open(self.settings_path) as f:
                return json.load(f)
        return {}

    def _save_settings(self, settings):
        os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
        with open(self.settings_path, "w") as f:
            json.dump(settings, f)

    # --- Methods callable from the frontend ---

    async def get_status(self):
        """Return current fan status."""
        if not self.fan:
            return {"error": "No fan controller found"}
        return {
            "rpm": self.fan.get_rpm(),
            "mode": self.fan.get_mode(),
            "curve_active": self.curve_task is not None,
        }

    async def get_temp(self):
        """Return current CPU temperature in Celsius."""
        temp_path = find_temp_sensor()
        if temp_path:
            with open(temp_path) as f:
                return int(f.read().strip()) / 1000
        return None

    async def set_fan_speed(self, percent: int):
        """Set a fixed manual fan speed (0-100%)."""
        await self.stop_curve()
        self.fan.set_manual(percent)
        self._save_settings({"mode": "manual", "speed": percent})

    async def set_auto(self):
        """Return to automatic EC fan control."""
        await self.stop_curve()
        self.fan.set_auto()
        self._save_settings({"mode": "auto"})

    async def start_curve(self, curve=None, interval=2.0):
        """Start the fan curve background loop."""
        await self.stop_curve()
        temp_sensor = find_temp_sensor()
        if not temp_sensor:
            return {"error": "No temperature sensor found"}
        c = curve or DEFAULT_CURVE
        self.curve_ctrl = FanCurveController(
            self.fan, temp_sensor, c, interval)
        self.curve_task = asyncio.create_task(
            self._run_curve_async())
        self._save_settings({
            "mode": "curve", "curve": c, "interval": interval})

    async def stop_curve(self):
        """Stop the fan curve loop."""
        if self.curve_ctrl:
            self.curve_ctrl._running = False
        if self.curve_task:
            self.curve_task.cancel()
            try:
                await self.curve_task
            except asyncio.CancelledError:
                pass
            self.curve_task = None
            self.curve_ctrl = None

    async def _run_curve_async(self):
        """Async wrapper for the fan curve loop."""
        ctrl = self.curve_ctrl
        ctrl._running = True
        last_speed = -1
        while ctrl._running:
            try:
                temp = ctrl.get_temp()
                target = ctrl.interpolate(temp)
                if abs(target - last_speed) > ctrl.hysteresis or last_speed < 0:
                    ctrl.fan.set_manual(target)
                    last_speed = target
                await asyncio.sleep(ctrl.interval)
            except Exception as e:
                decky.logger.error(f"Fan curve error: {e}")
                await asyncio.sleep(5)

    async def get_settings(self):
        """Return saved settings."""
        return self._load_settings()

    async def save_curve(self, curve):
        """Save a custom fan curve and apply it."""
        await self.start_curve(curve)
```

### 4e. Frontend (`src/index.tsx`)

```tsx
import {
  PanelSection,
  PanelSectionRow,
  SliderField,
  ToggleField,
  ButtonItem,
  Field,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { useState, useEffect, useCallback } from "react";
import { GiComputerFan } from "react-icons/gi";

// Backend RPC wrappers
const getStatus = callable<[], {
  rpm: number; mode: string; curve_active: boolean;
}>("get_status");
const getTemp = callable<[], number | null>("get_temp");
const setFanSpeed = callable<[percent: number], void>("set_fan_speed");
const setAuto = callable<[], void>("set_auto");
const startCurve = callable<[curve?: number[][], interval?: number], void>(
  "start_curve"
);
const stopCurve = callable<[], void>("stop_curve");

function FanControlPanel() {
  const [rpm, setRpm] = useState(0);
  const [temp, setTemp] = useState(0);
  const [mode, setMode] = useState("auto"); // "auto" | "manual" | "curve"
  const [manualSpeed, setManualSpeed] = useState(50);
  const [curveActive, setCurveActive] = useState(false);

  const refresh = useCallback(async () => {
    const status = await getStatus();
    if (status && !("error" in status)) {
      setRpm(status.rpm);
      setCurveActive(status.curve_active);
      if (status.curve_active) setMode("curve");
      else if (status.mode === "manual") setMode("manual");
      else setMode("auto");
    }
    const t = await getTemp();
    if (t !== null) setTemp(t);
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 3000);
    return () => clearInterval(interval);
  }, [refresh]);

  return (
    <PanelSection title="OXP Fan Control">
      {/* Status display */}
      <PanelSectionRow>
        <Field label="Fan Speed">{rpm} RPM</Field>
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label="CPU Temp">{temp.toFixed(1)}°C</Field>
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label="Mode">
          {curveActive ? "Fan Curve" : mode === "manual" ? "Manual" : "Auto"}
        </Field>
      </PanelSectionRow>

      {/* Auto mode toggle */}
      <PanelSectionRow>
        <ToggleField
          label="Manual Control"
          checked={mode !== "auto"}
          onChange={async (enabled) => {
            if (enabled) {
              await setFanSpeed(manualSpeed);
              setMode("manual");
            } else {
              await setAuto();
              setMode("auto");
            }
          }}
        />
      </PanelSectionRow>

      {/* Manual speed slider (visible when in manual mode) */}
      {mode === "manual" && !curveActive && (
        <PanelSectionRow>
          <SliderField
            label="Fan Speed %"
            value={manualSpeed}
            min={0}
            max={100}
            step={5}
            onChange={async (val) => {
              setManualSpeed(val);
              await setFanSpeed(val);
            }}
          />
        </PanelSectionRow>
      )}

      {/* Fan curve toggle */}
      <PanelSectionRow>
        <ToggleField
          label="Fan Curve"
          description="Auto-adjust speed based on temperature"
          checked={curveActive}
          onChange={async (enabled) => {
            if (enabled) {
              await startCurve();
              setCurveActive(true);
              setMode("curve");
            } else {
              await stopCurve();
              await setAuto();
              setCurveActive(false);
              setMode("auto");
            }
          }}
        />
      </PanelSectionRow>

      {/* Quick presets */}
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => setFanSpeed(0)}>
          Silent (Fans Off)
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => setFanSpeed(50)}>
          Balanced (50%)
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => setFanSpeed(100)}>
          Performance (100%)
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

export default definePlugin(() => ({
  name: "OXP Fan Control",
  content: <FanControlPanel />,
  icon: <GiComputerFan />,
  onDismount() {},
}));
```

---

## 5. Step-by-Step Implementation Walkthrough

### Step 1: Verify Hardware Access (30 min)

SSH into your Bazzite device and check what's available:

```bash
# Check if oxpec/oxp-sensors driver is loaded
ls /sys/class/hwmon/hwmon*/name | xargs -I{} sh -c 'echo "$(cat {}) → {}"'

# If not found, try loading it
sudo modprobe oxp-sensors  # or oxpec on newer kernels

# If the driver doesn't support Apex yet, use ec_sys
sudo modprobe ec_sys write_support=1
sudo xxd /sys/kernel/debug/ec/ec0/io | head -10

# Read fan RPM from EC directly (register 0x76, 2 bytes)
sudo xxd -s 0x76 -l 2 /sys/kernel/debug/ec/ec0/io

# Read PWM enable (0x4A) and PWM value (0x4B)
sudo xxd -s 0x4A -l 2 /sys/kernel/debug/ec/ec0/io

# Test manual fan control via EC
sudo python3 -c "
f = open('/sys/kernel/debug/ec/ec0/io', 'r+b')
f.seek(0x4A); f.write(b'\x01')  # manual mode
f.seek(0x4B); f.write(b'\x5c')  # ~50% (92/184)
f.close()
print('Fan set to ~50%')
"

# Verify fan speed changed, then restore auto
sudo python3 -c "
f = open('/sys/kernel/debug/ec/ec0/io', 'r+b')
f.seek(0x4A); f.write(b'\x00')  # auto mode
f.close()
print('Auto mode restored')
"
```

**Important**: The register addresses above are based on the OneXFly line. If your specific Apex variant uses different registers, you'll need to discover them by:
1. Dumping all 256 EC registers (`xxd /sys/kernel/debug/ec/ec0/io`)
2. Changing fan speed in BIOS/Windows and comparing dumps
3. Cross-referencing with the [oxpec driver source](https://github.com/Samsagax/oxp-sensors/blob/main/oxp-sensors.c)

### Step 2: Build and Test CLI (1-2 hours)

1. Copy the Python code from Phase 1 into a single file: `/usr/local/bin/oxp-fan-ctl`
2. `chmod +x /usr/local/bin/oxp-fan-ctl`
3. Test each command: `status`, `set`, `auto`, `max`, `curve`
4. Verify the fan actually changes speed (hold your hand near the vent, or check RPM readback)

### Step 3: Set Up Decky Plugin Scaffold (1 hour)

```bash
# On your development machine (not the handheld)
git clone https://github.com/SteamDeckHomebrew/decky-plugin-template oxp-fan-control
cd oxp-fan-control
pnpm install

# Edit plugin.json with your plugin name and root flag
# Copy fan_control.py into py_modules/
# Write main.py (backend)
# Write src/index.tsx (frontend)
```

### Step 4: Build and Deploy (30 min)

```bash
# Build frontend
pnpm run build

# Copy to device (replace DECK_IP with your handheld's IP)
rsync -avz --exclude node_modules ./ deck@DECK_IP:~/homebrew/plugins/oxp-fan-control/

# On the device, restart Decky
sudo systemctl restart plugin_loader.service
```

### Step 5: Test in Game Mode

1. Enter Game Mode
2. Open the Quick Access Menu (QAM) via the `...` button
3. Find the plugin icon in the sidebar
4. Test: toggle manual mode, adjust the slider, check RPM updates
5. Test: enable the fan curve, run a game, watch it react to temp changes

### Step 6: Iterate and Polish

- Add per-game profiles (save/load different curves)
- Add a fan curve visualization (if Decky supports canvas/SVG)
- Add a "quiet" mode that caps RPM below a certain temp threshold
- Handle edge cases: what happens if the plugin crashes (ensure auto mode is restored)

---

## 6. Key Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| EC registers differ on your specific Apex revision | Dump EC registers and compare with known map; fall back to manual discovery |
| `oxpec` driver not in your kernel | Use `ec_sys` direct access (DKMS doesn't work on immutable Bazzite); or load module via systemd `insmod` service (see §1) |
| Bazzite's immutable filesystem blocks installs | Decky plugins install to `~/homebrew/` (user space); CLI can go in `~/.local/bin/`; kernel modules via systemd service or `ostree unlock --hotfix` |
| Fan gets stuck at 0% (thermal danger) | Always restore auto on exit/crash; add a watchdog that sets auto if no writes in 30s |
| Conflict with HHD fan control | Disable HHD's fan control if both are running: `hhd --no-fan` or set `fan.mode=disabled` in HHD config |
| Decky plugin permissions | The `"root"` flag ensures the backend has sysfs write access |
| Sleep/suspend crashes (Strix Halo) | Apply kernel parameters: `amdgpu.cwsr_enable=0 iommu=pt`; avoid low TDP before sleep; see §7 for full workaround list |
| HHD → InputPlumber migration | Fan control is independent of both — talks directly to kernel hwmon/EC (see §1) |

---

## 7. Known Issues — Strix Halo / OneXFly Apex on Bazzite / SteamOS

The Apex only started shipping late January 2026, so there are **no device-specific Bazzite bug reports yet**. However, the AMD Strix Halo platform has well-documented issues from the GPD Win 5 and OneXFly F1 Pro that will almost certainly apply. Sleep/suspend is the biggest risk area.

### 7a. Sleep / Suspend Bugs (Multiple Overlapping Issues)

#### VPE Idle Timeout Hang (~8% of Resumes)
The GPU's Video Processing Engine (VPE) fires an idle handler 1 second after resume, causing an SMU hang. Strix Halo needs more settling time than older chips.

- **Root cause**: After resume, `vpe_idle_work_handler` fires to re-gate VPE, causing the SMU to hang and partially freeze GPU IPs.
- **Fix**: A kernel patch by Antheas increases `VPE_IDLE_TIMEOUT` from 1s to 2s in `drivers/gpu/drm/amd/amdgpu/amdgpu_vpe.c`. Improved reliability "from 4-25 suspends to 200+ (tested) suspends."
- **Status**: Submitted to `amd-gfx` mailing list. May or may not be in your kernel yet.
- **Source**: [amd-gfx mailing list](https://www.mail-archive.com/amd-gfx@lists.freedesktop.org/msg127724.html)

#### MES Firmware Hang on Resume
AMD's Micro Engine Scheduler firmware can hang during compute wave store/resume on Strix Halo.

- **Symptom**: GPU hang with MES firmware 0x80 error after suspend/resume.
- **Workaround**: Add kernel parameter `amdgpu.cwsr_enable=0`
- **Source**: [ROCm Issue #5590](https://github.com/ROCm/ROCm/issues/5590)

#### VRAM Eviction Crash on Suspend
Under heavy RAM usage, amdgpu can't evict VRAM to system RAM during suspend (no swap fallback).

- **Fix**: Patches to move VRAM eviction to suspend "prepare" phase were landed for kernel 6.14 but partially reverted due to deadlock concerns.
- **Source**: [nyanpasu64 blog post](https://nyanpasu64.gitlab.io/blog/amdgpu-sleep-wake-hang/)

#### OneXFly F1 Pro Sleep Behavior (Predecessor, Likely Similar)
- Screen turns off but fans/RGB stay on — device does not fully enter deep sleep.
- Low TDP settings break suspend entirely — use auto or high TDP.
- Glitchy resume with screen artifacts and audio crackling after wake.
- Screen doesn't come back from sleep at minimum brightness settings.
- **Source**: [Universal Blue Discord / AnswerOverflow](https://www.answeroverflow.com/m/1310453547254681703)

#### Bazzite Kernel-Specific Suspend Regression
Some users found that the Bazzite-specific kernel (`kernel-bazzite`) introduces post-suspend performance degradation not present with the stock Fedora kernel. Switching to `6.11.5-300.fc41.x86_64` from `6.11.5-307.bazzite.fc41.x86_64` resolved it.

### 7b. GPU Driver Bugs (Strix Halo)

- **GPU hangs with AI workloads + hardware video encoding** (e.g., ROCm + Sunshine streaming). [ROCm #5665](https://github.com/ROCm/ROCm/issues/5665)
- **GPU stuck in low power/idle clocks** with VRAM reporting underflow on kernel 6.14 + ROCm 7.1. [ROCm #5750](https://github.com/ROCm/ROCm/issues/5750)
- **MES 0x83 firmware GPU hang / memory access fault**. [ROCm #5724](https://github.com/ROCm/ROCm/issues/5724)
- **Bazzite 43.20260101 update broke Strix Halo 395+** entirely, requiring rollback to 43.20251210.1. [Bazzite #3818](https://github.com/ublue-os/bazzite/issues/3818)
- **Kernels older than 6.18.4** have stability issues on gfx1151 (Strix Halo).
- **Do NOT use linux-firmware-20251125** — it breaks ROCm and causes instability on Strix Halo.

### 7c. Memory Bandwidth Issue
On Strix Halo with Bazzite, memory bandwidth measured at ~55-65 GB/s instead of the expected ~225 GB/s. Switching to another distro on kernel 6.17 showed correct throughput. Appears Bazzite-specific. [Bazzite #3317](https://github.com/ublue-os/bazzite/issues/3317)

### 7d. Audio Issues (OneXFly Family)
- Internal audio not working on multiple OneXPlayer devices. External audio works. [Bazzite #941](https://github.com/ublue-os/bazzite/issues/941), [#1847](https://github.com/ublue-os/bazzite/issues/1847)
- Crackly audio after suspend/resume is a known general Bazzite/SteamOS issue.

### 7e. Display Issues (OneXFly Family)
- **Brightness crash**: Adjusting brightness in Game Mode turns screen off, requiring sleep/wake to restore. [Forum thread](https://universal-blue.discourse.group/t/onexplayer-onexfly-f1-pro-turning-off-the-screen-when-changing-brightness-through-the-steam-os-game-mode/10446)
- **Screen rotation incorrect** on F1 Pro OLED — required adding device to gamescope-session device-quirks. [Bazzite #2396](https://github.com/ublue-os/bazzite/issues/2396)
- **HDR enabled by default** with no toggle and washed-out display. [Bazzite #2369](https://github.com/ublue-os/bazzite/issues/2369)

### 7f. Controller / Input Issues (OneXFly Family)
- Home, Turbo, Virtual Keyboard buttons not working on OneXFly F1. [Bazzite #1635](https://github.com/ublue-os/bazzite/issues/1635)
- Shoulder buttons not functioning on F1 Pro OLED. [Bazzite #2397](https://github.com/ublue-os/bazzite/issues/2397)
- Joystick calibration must be done in OneXConsole on Windows before installing Bazzite.
- Fingerprint sensor wakes device from sleep on G1/X1. [Bazzite #2553](https://github.com/ublue-os/bazzite/issues/2553)

### 7g. VRAM Stuck at 4 GB
OneXFly BIOS doesn't expose VRAM allocation settings. Stuck at 4 GB by default. **Workaround**: Change VRAM allocation in Windows using OneXConsole app before installing Linux — the setting persists.

### 7h. Battery Reporting
F1 Pro reported to shut down without warning at 20% battery (misreads as 0%). OneXPlayer released EC firmware fix for units produced in January.

### 7i. SteamOS Boot Compatibility
- **SteamOS prior to 3.8 cannot boot** on Strix Halo at all (boot hang).
- SteamOS 3.8 was the first version to resolve this.

### 7j. Recommended Kernel Parameters for Strix Halo

```bash
# Apply all via rpm-ostree
rpm-ostree kargs --append-if-missing="amdgpu.cwsr_enable=0"
rpm-ostree kargs --append-if-missing="iommu=pt"
rpm-ostree kargs --append-if-missing="amdgpu.gttsize=126976"
rpm-ostree kargs --append-if-missing="ttm.pages_limit=32505856"
```

### 7k. Recommended Workarounds Summary

| Issue | Workaround |
|-------|-----------|
| VPE suspend hang (~8% of resumes) | Needs `VPE_IDLE_TIMEOUT` kernel patch (may need custom kernel build) |
| MES firmware GPU hang | `amdgpu.cwsr_enable=0` kernel parameter |
| Low memory bandwidth | `iommu=pt amdgpu.gttsize=126976 ttm.pages_limit=32505856` |
| VRAM stuck at 4 GB | Use OneXConsole in Windows before installing Linux |
| Joystick calibration | Calibrate in OneXConsole on Windows first |
| Sleep not fully working | Use higher TDP settings; avoid minimum brightness before sleep |
| Device spurious wake | `echo disabled > /sys/bus/i2c/devices/i2c-PNP0C50:00/power/wakeup` |
| Post-suspend perf drop | Try stock Fedora kernel instead of Bazzite kernel |
| SteamOS boot failure | Use SteamOS 3.8 or later |
| Bazzite 43.20260101 GPU crash | Roll back to 43.20251210.1 |

---

## 8. Useful References

### Fan Control / Kernel Driver
- **oxp-sensors kernel driver source**: [github.com/Samsagax/oxp-sensors](https://github.com/Samsagax/oxp-sensors)
- **oxp-platform-dkms** (out-of-tree driver): [github.com/Samsagax/oxp-platform-dkms](https://github.com/Samsagax/oxp-platform-dkms)
- **HHFC** (lightweight hwmon fan controller): [github.com/Samsagax/hhfc](https://github.com/Samsagax/hhfc)
- **Kernel hwmon sysfs ABI**: [kernel.org/doc/html/latest/hwmon/sysfs-interface.html](https://docs.kernel.org/hwmon/sysfs-interface.html)
- **EC register access**: [kernel.org/doc/Documentation/ABI/testing/debugfs-ec](https://www.kernel.org/doc/Documentation/ABI/testing/debugfs-ec)
- **Apex kernel patch v2 (Feb 23 2026)**: [LKML v2 1/4 — oxpec: Add support for OneXPlayer APEX](https://lkml.org/lkml/2026/2/23/1563)
- **Apex kernel patch cover letter**: [LKML v2 0/4 — oxpec: Add more devices](https://lkml.org/lkml/2026/2/23/1567)
- **Earlier v7 patch series (Mar 2025)**: [Patchwork — Add devices, features, fix ABI and move to platform/x86](https://patchwork.kernel.org/project/linux-pm/cover/20250319181044.392235-1-lkml@antheas.dev/)

### Decky Plugin Development
- **Decky plugin template**: [github.com/SteamDeckHomebrew/decky-plugin-template](https://github.com/SteamDeckHomebrew/decky-plugin-template)
- **SimpleDeckyTDP** (good reference Python Decky plugin): [github.com/aarron-lee/SimpleDeckyTDP](https://github.com/aarron-lee/SimpleDeckyTDP)
- **Decky Playground** (UI component demos): [github.com/SteamDeckHomebrew/decky-playground](https://github.com/SteamDeckHomebrew/decky-playground)

### Bazzite / SteamOS / Handheld Ecosystem
- **Bazzite OneXPlayer Handhelds Documentation**: [docs.bazzite.gg](https://docs.bazzite.gg/Handheld_and_HTPC_edition/Handheld_Wiki/OneXPlayer_Handhelds/)
- **Bazzite OneXPlayer Support Update (Oct 2024)**: [Universal Blue Forum](https://universal-blue.discourse.group/t/bazzite-update-onexplayer-support-ally-goodies/4517)
- **Bazzite Sleep Fixes Update (Jan 2025)**: [Universal Blue Forum](https://universal-blue.discourse.group/t/bazzite-update-happy-new-year-sleep-fixes-smoother-updates-bootc-fan-curves-gpd-more-devices/6200)
- **Handheld Daemon (HHD)**: [github.com/hhd-dev/hhd](https://github.com/hhd-dev/hhd)
- **InputPlumber**: [github.com/ShadowBlip/InputPlumber](https://github.com/ShadowBlip/InputPlumber)
- **BlueBuild (custom Bazzite images)**: [github.com/ublue-os/image-template](https://github.com/ublue-os/image-template)

### Strix Halo Platform Issues
- **VPE Idle Timeout Patch**: [amd-gfx mailing list](https://www.mail-archive.com/amd-gfx@lists.freedesktop.org/msg127724.html)
- **MES firmware hang workaround**: [ROCm #5590](https://github.com/ROCm/ROCm/issues/5590)
- **GPU hang with AI + video encoding**: [ROCm #5665](https://github.com/ROCm/ROCm/issues/5665)
- **MES 0x83 GPU memory fault**: [ROCm #5724](https://github.com/ROCm/ROCm/issues/5724)
- **GPU stuck in low power clocks**: [ROCm #5750](https://github.com/ROCm/ROCm/issues/5750)
- **Bazzite update broke Strix Halo**: [Bazzite #3818](https://github.com/ublue-os/bazzite/issues/3818)
- **Low memory bandwidth on Bazzite**: [Bazzite #3317](https://github.com/ublue-os/bazzite/issues/3317)
- **OneXPlayer screen won't wake after suspend**: [Bazzite #2081](https://github.com/ublue-os/bazzite/issues/2081)
- **OneXFly button issues**: [Bazzite #1635](https://github.com/ublue-os/bazzite/issues/1635)
- **F1 Pro screen rotation**: [Bazzite #2396](https://github.com/ublue-os/bazzite/issues/2396)
- **F1 Pro shoulder buttons**: [Bazzite #2397](https://github.com/ublue-os/bazzite/issues/2397)
- **Audio issues**: [Bazzite #941](https://github.com/ublue-os/bazzite/issues/941), [#1847](https://github.com/ublue-os/bazzite/issues/1847)
- **Fingerprint wakes device**: [Bazzite #2553](https://github.com/ublue-os/bazzite/issues/2553)
- **GPD Win 5 Bazzite Support**: [Universal Blue Forum](https://universal-blue.discourse.group/t/gpd-win-5-bazzite-support/10735)
- **amdgpu sleep-wake hang blog post**: [nyanpasu64.gitlab.io](https://nyanpasu64.gitlab.io/blog/amdgpu-sleep-wake-hang/)
- **SteamOS 3.7 handheld support**: [videocardz.com](https://videocardz.com/newz/valve-steamos-3-7-stable-released-full-legion-go-s-support-and-limited-support-for-other-amd-powered-handhelds)
- **GPD Win 5 SteamOS vs Windows performance**: [NotebookCheck](https://www.notebookcheck.net/This-1-500-Strix-Halo-handheld-runs-Cyberpunk-2077-on-SteamOS-at-1080p-Ultra-45-W-scoring-71-FPS-here-s-how-it-compares-with-Windows-11-performance.1150543.0.html)
