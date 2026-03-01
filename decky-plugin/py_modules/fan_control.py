"""Fan control for OneXPlayer Apex.

The Apex uses an embedded controller (EC) to manage fan speed. This module
provides three fallback backends to talk to the EC, tried in order:

  1. hwmon — uses the oxpec kernel driver's sysfs interface (cleanest)
  2. EC debugfs — reads/writes EC registers directly via /sys/kernel/debug/ec/ec0/io
  3. Port I/O — reads/writes EC registers via /dev/port (lowest-level, always works)

Also provides a temperature-based fan curve controller (FanCurveRunner) with
predefined profiles (silent, balanced, performance).
"""

import asyncio
import glob
import os
import subprocess
import time
import logging

logger = logging.getLogger("OXP-FanControl")


def _clean_env():
    """Return a subprocess environment without PyInstaller's LD_LIBRARY_PATH."""
    env = os.environ.copy()
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        env.pop(var, None)
    return env

# ── sysfs paths ──
HWMON_BASE = "/sys/class/hwmon"
# Known kernel driver names for the OneXPlayer EC sensor module
DRIVER_NAMES = ["oxpec", "oxp_ec", "oxp-sensors"]

# ── EC register interface (debugfs) ──
EC_IO = "/sys/kernel/debug/ec/ec0/io"

# EC register addresses (Apex-specific, found by reverse-engineering the EC firmware)
EC_PWM_ENABLE = 0x4A  # 0x00 = auto (EC controls fan), 0x01 = manual (we control fan)
EC_PWM_VALUE = 0x4B   # Fan duty cycle: 0-184 (not 0-255 like standard PWM)
EC_FAN_RPM = 0x78     # Fan RPM: 2 bytes, little-endian

# ── EC ACPI port I/O interface ──
# Standard ACPI embedded controller ports for command/data exchange
EC_DATA_PORT = 0x62   # Read/write data to/from EC
EC_CMD_PORT = 0x66    # Send commands to EC / read EC status
EC_CMD_READ = 0x80    # EC command: read register
EC_CMD_WRITE = 0x81   # EC command: write register

# ── Fan speed constants ──
PWM_MAX = 255          # sysfs standard PWM range (0-255)
NATIVE_PWM_MAX = 184   # EC native duty cycle range (0-184)
DEV_PORT = "/dev/port" # Linux character device for direct I/O port access

# Predefined fan curves: list of (temp_celsius, fan_percent) breakpoints.
# Temperatures between breakpoints are linearly interpolated.
PROFILES = {
    "silent": [(40, 15), (50, 20), (60, 30), (70, 40), (80, 55), (90, 75)],
    "balanced": [(40, 0), (50, 20), (60, 40), (70, 60), (80, 80), (90, 100)],
    "performance": [(40, 20), (50, 40), (60, 60), (70, 80), (80, 100), (90, 100)],
}


def find_hwmon():
    """Find the oxpec hwmon device path, if the kernel driver is loaded."""
    for hwmon in sorted(glob.glob(f"{HWMON_BASE}/hwmon*")):
        name_file = os.path.join(hwmon, "name")
        if os.path.exists(name_file):
            with open(name_file) as f:
                name = f.read().strip()
            if name in DRIVER_NAMES:
                return hwmon
    return None


def find_temp_sensor():
    """Find the best CPU/APU temperature sensor.

    Prefers AMD k10temp or zenpower drivers (accurate Tctl/Tdie reading),
    falls back to the first available temp sensor.
    """
    # First pass: look for AMD-specific temp drivers
    for hwmon in sorted(glob.glob(f"{HWMON_BASE}/hwmon*")):
        name_file = os.path.join(hwmon, "name")
        if os.path.exists(name_file):
            with open(name_file) as f:
                name = f.read().strip()
            if name in ["k10temp", "zenpower"]:
                temp_file = os.path.join(hwmon, "temp1_input")
                if os.path.exists(temp_file):
                    return temp_file
    # Fallback: use any available temperature sensor
    for hwmon in sorted(glob.glob(f"{HWMON_BASE}/hwmon*")):
        for temp in sorted(glob.glob(os.path.join(hwmon, "temp*_input"))):
            return temp
    return None


class HwmonFanController:
    """Fan control via the oxpec hwmon sysfs interface.

    This is the cleanest backend — it uses the standard Linux hwmon
    sysfs interface exposed by the oxpec kernel driver. Requires the
    driver to be loaded (modprobe oxpec).
    """

    def __init__(self, hwmon_path):
        self.hwmon_path = hwmon_path
        self.pwm_path = os.path.join(hwmon_path, "pwm1")
        self.pwm_enable_path = os.path.join(hwmon_path, "pwm1_enable")
        self.fan_input_path = os.path.join(hwmon_path, "fan1_input")
        self.backend_name = f"hwmon ({hwmon_path})"

    def get_rpm(self):
        """Read current fan RPM from sysfs."""
        with open(self.fan_input_path) as f:
            return int(f.read().strip())

    def get_pwm(self):
        """Read current PWM duty cycle (0-255)."""
        with open(self.pwm_path) as f:
            return int(f.read().strip())

    def get_percent(self):
        """Convert PWM value to percentage (0-100)."""
        return self.get_pwm() / PWM_MAX * 100

    def get_mode(self):
        """Read fan control mode from sysfs.

        pwm1_enable values: 0=full speed, 1=manual, 2=auto
        """
        with open(self.pwm_enable_path) as f:
            val = int(f.read().strip())
        return {0: "full", 1: "manual", 2: "auto"}.get(val, f"unknown({val})")

    def set_auto(self):
        """Hand fan control back to the EC firmware."""
        with open(self.pwm_enable_path, "w") as f:
            f.write("2")

    def set_manual(self, percent):
        """Set fan to manual mode at the given speed percentage (0-100)."""
        pwm_value = int(percent / 100 * PWM_MAX)
        pwm_value = max(0, min(PWM_MAX, pwm_value))
        # Switch to manual mode first, then set speed
        with open(self.pwm_enable_path, "w") as f:
            f.write("1")
        with open(self.pwm_path, "w") as f:
            f.write(str(pwm_value))


class ECFanController:
    """Fan control via direct EC register access (ec_sys kernel module).

    Reads/writes EC registers through /sys/kernel/debug/ec/ec0/io.
    Requires: modprobe ec_sys write_support=1
    """

    def __init__(self):
        # Try to load the ec_sys module with write support if not already present
        if not os.path.exists(EC_IO):
            subprocess.run(
                ["modprobe", "ec_sys", "write_support=1"],
                capture_output=True, timeout=10,
                env=_clean_env(),
            )
        if not os.path.exists(EC_IO):
            raise RuntimeError("Cannot access EC: ec_sys module not available")
        self.backend_name = f"direct EC ({EC_IO})"

    def _read_byte(self, offset):
        """Read a single byte from an EC register."""
        with open(EC_IO, "rb") as f:
            f.seek(offset)
            return f.read(1)[0]

    def _write_byte(self, offset, value):
        """Write a single byte to an EC register."""
        with open(EC_IO, "r+b") as f:
            f.seek(offset)
            f.write(bytes([value]))

    def _read_word(self, offset):
        """Read a 16-bit little-endian word from two consecutive EC registers."""
        with open(EC_IO, "rb") as f:
            f.seek(offset)
            data = f.read(2)
        return int.from_bytes(data, "little")

    def get_rpm(self):
        """Read fan RPM from the EC (2-byte little-endian at register 0x78)."""
        return self._read_word(EC_FAN_RPM)

    def get_pwm(self):
        """Read current PWM duty cycle from EC (0-184)."""
        return self._read_byte(EC_PWM_VALUE)

    def get_percent(self):
        """Convert native PWM value to percentage (0-100)."""
        return self.get_pwm() / NATIVE_PWM_MAX * 100

    def get_mode(self):
        """Read fan control mode from the EC enable register."""
        val = self._read_byte(EC_PWM_ENABLE)
        return "manual" if val == 0x01 else "auto"

    def set_auto(self):
        """Return fan control to the EC firmware (register 0x4A = 0x00)."""
        self._write_byte(EC_PWM_ENABLE, 0x00)

    def set_manual(self, percent):
        """Take manual control and set fan speed (register 0x4A = 0x01, 0x4B = duty)."""
        native_val = int(percent / 100 * NATIVE_PWM_MAX)
        native_val = max(0, min(NATIVE_PWM_MAX, native_val))
        self._write_byte(EC_PWM_ENABLE, 0x01)
        self._write_byte(EC_PWM_VALUE, native_val)


class PortIOFanController:
    """Fan control via direct I/O port access (/dev/port).

    This is the lowest-level backend. It talks to the EC using the standard
    ACPI EC protocol over I/O ports 0x62 (data) and 0x66 (command/status).

    The protocol for reading a register:
      1. Wait for EC input buffer to be free (IBF clear)
      2. Write READ command (0x80) to command port
      3. Wait for IBF clear again
      4. Write register address to data port
      5. Wait for EC output buffer to have data (OBF set)
      6. Read the value from data port

    Writing is similar but uses command 0x81 and writes value after address.
    """

    def __init__(self):
        if not os.path.exists(DEV_PORT):
            raise RuntimeError(f"{DEV_PORT} not found")
        # Verify we can actually read from the port
        try:
            with open(DEV_PORT, "rb") as f:
                f.seek(EC_CMD_PORT)
                f.read(1)
        except PermissionError:
            raise RuntimeError(f"Cannot read {DEV_PORT}: permission denied")
        self.backend_name = f"port I/O ({DEV_PORT})"

    def _inb(self, port):
        """Read one byte from an I/O port."""
        with open(DEV_PORT, "rb") as f:
            f.seek(port)
            return f.read(1)[0]

    def _outb(self, port, value):
        """Write one byte to an I/O port."""
        with open(DEV_PORT, "r+b") as f:
            f.seek(port)
            f.write(bytes([value]))

    def _wait_ec_ibf_clear(self, timeout=0.5):
        """Wait for EC input buffer flag to clear (ready to accept commands).

        Bit 1 of the status register indicates the input buffer is full.
        We must wait for it to clear before sending new data.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self._inb(EC_CMD_PORT)
            if not (status & 0x02):  # IBF is bit 1
                return
            time.sleep(0.001)
        raise TimeoutError("EC input buffer not ready")

    def _wait_ec_obf_set(self, timeout=0.5):
        """Wait for EC output buffer flag to be set (data available to read).

        Bit 0 of the status register indicates data is available in the
        output buffer.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self._inb(EC_CMD_PORT)
            if status & 0x01:  # OBF is bit 0
                return
            time.sleep(0.001)
        raise TimeoutError("EC output buffer not ready")

    def _drain_obf(self):
        """Drain any stale data from the EC output buffer.

        Sometimes the EC has leftover data from previous operations.
        We read and discard it to get a clean state before our command.
        """
        for _ in range(16):
            status = self._inb(EC_CMD_PORT)
            if not (status & 0x01):
                return
            self._inb(EC_DATA_PORT)  # discard stale byte
            time.sleep(0.001)

    def _ec_read(self, reg, retries=3):
        """Read a single EC register using the ACPI EC protocol.

        Retries on timeout since the EC can occasionally be busy
        servicing other requests (e.g. from the BIOS or OS ACPI layer).
        """
        for attempt in range(retries):
            try:
                self._drain_obf()
                self._wait_ec_ibf_clear()
                self._outb(EC_CMD_PORT, EC_CMD_READ)   # send READ command
                self._wait_ec_ibf_clear()
                self._outb(EC_DATA_PORT, reg)           # send register address
                self._wait_ec_obf_set()
                val = self._inb(EC_DATA_PORT)           # read the value
                time.sleep(0.01)  # small delay between operations
                return val
            except TimeoutError:
                if attempt < retries - 1:
                    time.sleep(0.05)
                    self._drain_obf()
                else:
                    raise

    def _ec_write(self, reg, value, retries=3):
        """Write a single EC register using the ACPI EC protocol."""
        for attempt in range(retries):
            try:
                self._drain_obf()
                self._wait_ec_ibf_clear()
                self._outb(EC_CMD_PORT, EC_CMD_WRITE)   # send WRITE command
                self._wait_ec_ibf_clear()
                self._outb(EC_DATA_PORT, reg)            # send register address
                self._wait_ec_ibf_clear()
                self._outb(EC_DATA_PORT, value)          # send the value
                time.sleep(0.01)
                return
            except TimeoutError:
                if attempt < retries - 1:
                    time.sleep(0.05)
                    self._drain_obf()
                else:
                    raise

    def get_rpm(self):
        """Read fan RPM (2-byte little-endian at register 0x78).

        Takes 5 readings and returns the median to filter out
        occasional garbage values from the EC.
        """
        readings = []
        for _ in range(5):
            try:
                lo = self._ec_read(EC_FAN_RPM)
                hi = self._ec_read(EC_FAN_RPM + 1)
                rpm = (hi << 8) | lo
                if rpm < 10000:  # sanity check — real RPM should be well under 10k
                    readings.append(rpm)
            except TimeoutError:
                pass
            time.sleep(0.05)
        if not readings:
            return -1  # indicate read failure
        readings.sort()
        return readings[len(readings) // 2]  # median

    def get_pwm(self):
        """Read current PWM duty cycle from EC (0-184)."""
        return self._ec_read(EC_PWM_VALUE)

    def get_percent(self):
        """Convert native PWM value to percentage (0-100)."""
        return self.get_pwm() / NATIVE_PWM_MAX * 100

    def get_mode(self):
        """Read fan control mode from the EC enable register."""
        val = self._ec_read(EC_PWM_ENABLE)
        return "manual" if val == 0x01 else "auto"

    def set_auto(self):
        """Return fan control to the EC firmware."""
        self._ec_write(EC_PWM_ENABLE, 0x00)

    def set_manual(self, percent):
        """Take manual control and set fan speed."""
        native_val = int(percent / 100 * NATIVE_PWM_MAX)
        native_val = max(0, min(NATIVE_PWM_MAX, native_val))
        self._ec_write(EC_PWM_ENABLE, 0x01)   # switch to manual
        self._ec_write(EC_PWM_VALUE, native_val)  # set speed


def get_controller():
    """Auto-detect and return the best available fan controller.

    Tries backends in order of preference: hwmon > EC debugfs > port I/O.
    Raises RuntimeError if no backend is available.
    """
    # 1. Try hwmon (cleanest — uses kernel driver)
    hwmon = find_hwmon()
    if hwmon:
        logger.info(f"Using hwmon driver at {hwmon}")
        return HwmonFanController(hwmon)
    # 2. Try direct EC access via debugfs
    try:
        ctrl = ECFanController()
        logger.info("Using direct EC access via ec_sys")
        return ctrl
    except RuntimeError:
        pass
    # 3. Try raw port I/O (always works if we have permissions)
    try:
        ctrl = PortIOFanController()
        logger.info("Using direct port I/O (/dev/port)")
        return ctrl
    except RuntimeError:
        pass
    raise RuntimeError("No fan control backend available")


class FanCurveRunner:
    """Runs a temperature-based fan curve as an async background task.

    Periodically reads the CPU temperature, interpolates the target fan
    speed from the curve, and applies it. Uses hysteresis to avoid
    constantly changing fan speed for small temp fluctuations.
    """

    def __init__(self, fan_ctrl, temp_sensor_path, curve, interval=2.0, hysteresis=3):
        self.fan = fan_ctrl
        self.temp_path = temp_sensor_path
        self.curve = curve              # list of (temp_c, fan_percent) breakpoints
        self.interval = interval        # seconds between adjustments
        self.hysteresis = hysteresis    # minimum speed change (%) to bother applying
        self._task = None
        self._last_speed = -1           # last applied speed (for hysteresis comparison)

    def get_temp(self):
        """Read current CPU temperature in degrees Celsius."""
        with open(self.temp_path) as f:
            return int(f.read().strip()) / 1000  # millidegrees to degrees

    def interpolate(self, temp):
        """Linearly interpolate fan speed from the curve breakpoints.

        For a temp between two breakpoints, returns a proportionally
        scaled speed. Below/above the curve range, clamps to the
        first/last breakpoint's speed.
        """
        # Below the curve — use the lowest breakpoint's speed
        if temp <= self.curve[0][0]:
            return self.curve[0][1]
        # Above the curve — use the highest breakpoint's speed
        if temp >= self.curve[-1][0]:
            return self.curve[-1][1]
        # Between two breakpoints — linear interpolation
        for i in range(len(self.curve) - 1):
            t0, s0 = self.curve[i]
            t1, s1 = self.curve[i + 1]
            if t0 <= temp <= t1:
                ratio = (temp - t0) / (t1 - t0)
                return s0 + ratio * (s1 - s0)
        return self.curve[-1][1]

    async def _run_loop(self):
        """Main fan curve loop — reads temp and adjusts fan speed."""
        try:
            while True:
                temp = self.get_temp()
                target = self.interpolate(temp)
                # Only apply if the change exceeds hysteresis threshold
                if abs(target - self._last_speed) > self.hysteresis or self._last_speed < 0:
                    self.fan.set_manual(target)
                    self._last_speed = target
                    logger.info(f"Fan curve: {temp:.1f}°C → {target:.0f}%")
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            pass

    def start(self, loop):
        """Start the fan curve as an async task on the given event loop."""
        if self._task is None or self._task.done():
            self._last_speed = -1
            self._task = loop.create_task(self._run_loop())

    async def stop(self):
        """Stop the fan curve task. Does NOT restore auto mode — caller should do that."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
