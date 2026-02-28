"""Fan control for OneXPlayer Apex.

Provides three fallback backends (hwmon, EC debugfs, port I/O) and a
temperature-based fan curve controller with predefined profiles.
"""

import asyncio
import glob
import os
import time
import logging

logger = logging.getLogger("OXP-FanControl")

HWMON_BASE = "/sys/class/hwmon"
DRIVER_NAMES = ["oxpec", "oxp_ec", "oxp-sensors"]

EC_IO = "/sys/kernel/debug/ec/ec0/io"

# EC register addresses (Apex-specific)
EC_PWM_ENABLE = 0x4A  # 0x00 = auto, 0x01 = manual
EC_PWM_VALUE = 0x4B   # 0-184 duty cycle
EC_FAN_RPM = 0x78     # 2 bytes little-endian

# EC I/O ports
EC_DATA_PORT = 0x62
EC_CMD_PORT = 0x66
EC_CMD_READ = 0x80
EC_CMD_WRITE = 0x81

PWM_MAX = 255          # sysfs standard range
NATIVE_PWM_MAX = 184   # EC native range
DEV_PORT = "/dev/port"

# Predefined fan profiles: list of (temp_celsius, fan_percent) tuples
PROFILES = {
    "silent": [(40, 0), (50, 10), (60, 20), (70, 35), (80, 50), (90, 70)],
    "balanced": [(40, 0), (50, 20), (60, 40), (70, 60), (80, 80), (90, 100)],
    "performance": [(40, 20), (50, 40), (60, 60), (70, 80), (80, 100), (90, 100)],
}


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
            if name in ["k10temp", "zenpower"]:
                temp_file = os.path.join(hwmon, "temp1_input")
                if os.path.exists(temp_file):
                    return temp_file
    for hwmon in sorted(glob.glob(f"{HWMON_BASE}/hwmon*")):
        for temp in sorted(glob.glob(os.path.join(hwmon, "temp*_input"))):
            return temp
    return None


class HwmonFanController:
    """Fan control via the oxpec hwmon sysfs interface."""

    def __init__(self, hwmon_path):
        self.hwmon_path = hwmon_path
        self.pwm_path = os.path.join(hwmon_path, "pwm1")
        self.pwm_enable_path = os.path.join(hwmon_path, "pwm1_enable")
        self.fan_input_path = os.path.join(hwmon_path, "fan1_input")
        self.backend_name = f"hwmon ({hwmon_path})"

    def get_rpm(self):
        with open(self.fan_input_path) as f:
            return int(f.read().strip())

    def get_pwm(self):
        with open(self.pwm_path) as f:
            return int(f.read().strip())

    def get_percent(self):
        return self.get_pwm() / PWM_MAX * 100

    def get_mode(self):
        with open(self.pwm_enable_path) as f:
            val = int(f.read().strip())
        return {0: "full", 1: "manual", 2: "auto"}.get(val, f"unknown({val})")

    def set_auto(self):
        with open(self.pwm_enable_path, "w") as f:
            f.write("2")

    def set_manual(self, percent):
        pwm_value = int(percent / 100 * PWM_MAX)
        pwm_value = max(0, min(PWM_MAX, pwm_value))
        with open(self.pwm_enable_path, "w") as f:
            f.write("1")
        with open(self.pwm_path, "w") as f:
            f.write(str(pwm_value))


class ECFanController:
    """Fan control via direct EC register access (ec_sys module)."""

    def __init__(self):
        if not os.path.exists(EC_IO):
            os.system("modprobe ec_sys write_support=1 2>/dev/null")
        if not os.path.exists(EC_IO):
            raise RuntimeError("Cannot access EC: ec_sys module not available")
        self.backend_name = f"direct EC ({EC_IO})"

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
        return self._read_byte(EC_PWM_VALUE)

    def get_percent(self):
        return self.get_pwm() / NATIVE_PWM_MAX * 100

    def get_mode(self):
        val = self._read_byte(EC_PWM_ENABLE)
        return "manual" if val == 0x01 else "auto"

    def set_auto(self):
        self._write_byte(EC_PWM_ENABLE, 0x00)

    def set_manual(self, percent):
        native_val = int(percent / 100 * NATIVE_PWM_MAX)
        native_val = max(0, min(NATIVE_PWM_MAX, native_val))
        self._write_byte(EC_PWM_ENABLE, 0x01)
        self._write_byte(EC_PWM_VALUE, native_val)


class PortIOFanController:
    """Fan control via direct I/O port access (/dev/port)."""

    def __init__(self):
        if not os.path.exists(DEV_PORT):
            raise RuntimeError(f"{DEV_PORT} not found")
        try:
            with open(DEV_PORT, "rb") as f:
                f.seek(EC_CMD_PORT)
                f.read(1)
        except PermissionError:
            raise RuntimeError(f"Cannot read {DEV_PORT}: permission denied")
        self.backend_name = f"port I/O ({DEV_PORT})"

    def _inb(self, port):
        with open(DEV_PORT, "rb") as f:
            f.seek(port)
            return f.read(1)[0]

    def _outb(self, port, value):
        with open(DEV_PORT, "r+b") as f:
            f.seek(port)
            f.write(bytes([value]))

    def _wait_ec_ibf_clear(self, timeout=0.5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self._inb(EC_CMD_PORT)
            if not (status & 0x02):
                return
            time.sleep(0.001)
        raise TimeoutError("EC input buffer not ready")

    def _wait_ec_obf_set(self, timeout=0.5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self._inb(EC_CMD_PORT)
            if status & 0x01:
                return
            time.sleep(0.001)
        raise TimeoutError("EC output buffer not ready")

    def _drain_obf(self):
        for _ in range(16):
            status = self._inb(EC_CMD_PORT)
            if not (status & 0x01):
                return
            self._inb(EC_DATA_PORT)
            time.sleep(0.001)

    def _ec_read(self, reg, retries=3):
        for attempt in range(retries):
            try:
                self._drain_obf()
                self._wait_ec_ibf_clear()
                self._outb(EC_CMD_PORT, EC_CMD_READ)
                self._wait_ec_ibf_clear()
                self._outb(EC_DATA_PORT, reg)
                self._wait_ec_obf_set()
                val = self._inb(EC_DATA_PORT)
                time.sleep(0.01)
                return val
            except TimeoutError:
                if attempt < retries - 1:
                    time.sleep(0.05)
                    self._drain_obf()
                else:
                    raise

    def _ec_write(self, reg, value, retries=3):
        for attempt in range(retries):
            try:
                self._drain_obf()
                self._wait_ec_ibf_clear()
                self._outb(EC_CMD_PORT, EC_CMD_WRITE)
                self._wait_ec_ibf_clear()
                self._outb(EC_DATA_PORT, reg)
                self._wait_ec_ibf_clear()
                self._outb(EC_DATA_PORT, value)
                time.sleep(0.01)
                return
            except TimeoutError:
                if attempt < retries - 1:
                    time.sleep(0.05)
                    self._drain_obf()
                else:
                    raise

    def get_rpm(self):
        readings = []
        for _ in range(5):
            try:
                lo = self._ec_read(EC_FAN_RPM)
                hi = self._ec_read(EC_FAN_RPM + 1)
                rpm = (hi << 8) | lo
                if rpm < 10000:
                    readings.append(rpm)
            except TimeoutError:
                pass
            time.sleep(0.05)
        if not readings:
            return -1
        readings.sort()
        return readings[len(readings) // 2]

    def get_pwm(self):
        return self._ec_read(EC_PWM_VALUE)

    def get_percent(self):
        return self.get_pwm() / NATIVE_PWM_MAX * 100

    def get_mode(self):
        val = self._ec_read(EC_PWM_ENABLE)
        return "manual" if val == 0x01 else "auto"

    def set_auto(self):
        self._ec_write(EC_PWM_ENABLE, 0x00)

    def set_manual(self, percent):
        native_val = int(percent / 100 * NATIVE_PWM_MAX)
        native_val = max(0, min(NATIVE_PWM_MAX, native_val))
        self._ec_write(EC_PWM_ENABLE, 0x01)
        self._ec_write(EC_PWM_VALUE, native_val)


def get_controller():
    """Auto-detect and return the best available fan controller."""
    hwmon = find_hwmon()
    if hwmon:
        logger.info(f"Using hwmon driver at {hwmon}")
        return HwmonFanController(hwmon)
    try:
        ctrl = ECFanController()
        logger.info("Using direct EC access via ec_sys")
        return ctrl
    except RuntimeError:
        pass
    try:
        ctrl = PortIOFanController()
        logger.info("Using direct port I/O (/dev/port)")
        return ctrl
    except RuntimeError:
        pass
    raise RuntimeError("No fan control backend available")


class FanCurveRunner:
    """Runs a fan curve loop as an async task."""

    def __init__(self, fan_ctrl, temp_sensor_path, curve, interval=2.0, hysteresis=2):
        self.fan = fan_ctrl
        self.temp_path = temp_sensor_path
        self.curve = curve
        self.interval = interval
        self.hysteresis = hysteresis
        self._task = None
        self._last_speed = -1

    def get_temp(self):
        with open(self.temp_path) as f:
            return int(f.read().strip()) / 1000

    def interpolate(self, temp):
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

    async def _run_loop(self):
        try:
            while True:
                temp = self.get_temp()
                target = self.interpolate(temp)
                if abs(target - self._last_speed) > self.hysteresis or self._last_speed < 0:
                    self.fan.set_manual(target)
                    self._last_speed = target
                    logger.info(f"Fan curve: {temp:.1f}°C → {target:.0f}%")
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            pass

    def start(self, loop):
        if self._task is None or self._task.done():
            self._last_speed = -1
            self._task = loop.create_task(self._run_loop())

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
