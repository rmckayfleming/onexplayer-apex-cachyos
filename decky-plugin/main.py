"""OneXPlayer Apex Tools — Decky Loader plugin backend.

Exposes methods for button fix, sleep fix, home button monitor,
and fan control to the frontend via Decky's RPC bridge.
"""

import asyncio
import logging
import os
import sys

# Add py_modules to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "py_modules"))

from fan_control import (
    FanCurveRunner,
    PROFILES,
    find_temp_sensor,
    get_controller,
)
from button_fix import apply as apply_button_fix_impl, is_applied as button_fix_status
from sleep_fix import apply as apply_sleep_fix_impl, get_status as sleep_fix_status
from home_button import HomeButtonMonitor

logging.basicConfig(
    level=logging.INFO,
    format="[OXP-Apex] %(name)s: %(message)s",
)
logger = logging.getLogger("OXP-Plugin")


class Plugin:
    fan_ctrl = None
    fan_curve_runner = None
    fan_mode = "auto"       # "auto" or "manual"
    fan_profile = "custom"  # "silent", "balanced", "performance", "custom"
    fan_speed = 50          # manual slider value (0-100)
    home_monitor = None

    async def _main(self):
        """Plugin entry point — called by Decky on load."""
        logger.info("OneXPlayer Apex Tools starting")
        # Init fan controller (best-effort — may fail if no backend)
        try:
            self.fan_ctrl = get_controller()
        except RuntimeError as e:
            logger.error(f"Fan control init failed: {e}")
            self.fan_ctrl = None
        self.home_monitor = HomeButtonMonitor()

    async def _unload(self):
        """Plugin teardown — called by Decky on unload."""
        logger.info("OneXPlayer Apex Tools unloading")
        # Stop home button monitor
        if self.home_monitor:
            await self.home_monitor.stop()
        # Stop fan curve and restore auto
        if self.fan_curve_runner:
            await self.fan_curve_runner.stop()
        if self.fan_ctrl:
            try:
                self.fan_ctrl.set_auto()
            except Exception:
                pass

    # ── Status overview ──

    async def get_status(self):
        """Get combined status of all features."""
        fan_status = await self.get_fan_status()
        return {
            "button_fix": button_fix_status(),
            "sleep_fix": sleep_fix_status(),
            "home_button": self.home_monitor.is_running if self.home_monitor else False,
            "fan": fan_status,
        }

    # ── Button Fix ──

    async def get_button_fix_status(self):
        return button_fix_status()

    async def apply_button_fix(self):
        return apply_button_fix_impl()

    # ── Sleep Fix ──

    async def get_sleep_fix_status(self):
        return sleep_fix_status()

    async def apply_sleep_fix(self):
        return apply_sleep_fix_impl()

    # ── Home Button Monitor ──

    async def get_home_button_status(self):
        return {"running": self.home_monitor.is_running if self.home_monitor else False}

    async def start_home_button(self):
        if not self.home_monitor:
            self.home_monitor = HomeButtonMonitor()
        loop = asyncio.get_event_loop()
        self.home_monitor.start(loop)
        return {"running": True}

    async def stop_home_button(self):
        if self.home_monitor:
            await self.home_monitor.stop()
        return {"running": False}

    # ── Fan Control ──

    async def get_fan_status(self):
        if not self.fan_ctrl:
            return {"available": False, "error": "No fan control backend"}
        try:
            rpm = self.fan_ctrl.get_rpm()
            percent = self.fan_ctrl.get_percent()
            mode = self.fan_ctrl.get_mode()
            temp_path = find_temp_sensor()
            temp = None
            if temp_path:
                with open(temp_path) as f:
                    temp = int(f.read().strip()) / 1000
            return {
                "available": True,
                "rpm": rpm,
                "percent": round(percent, 1),
                "hw_mode": mode,
                "temp": round(temp, 1) if temp is not None else None,
                "mode": self.fan_mode,
                "profile": self.fan_profile,
                "speed": self.fan_speed,
                "backend": self.fan_ctrl.backend_name,
            }
        except Exception as e:
            return {"available": False, "error": str(e)}

    async def set_fan_mode(self, mode):
        """Set fan mode: 'auto' or 'manual'."""
        if not self.fan_ctrl:
            return {"success": False, "error": "No fan control backend"}
        self.fan_mode = mode
        if mode == "auto":
            # Stop any running curve
            if self.fan_curve_runner:
                await self.fan_curve_runner.stop()
                self.fan_curve_runner = None
            self.fan_ctrl.set_auto()
            return {"success": True, "mode": "auto"}
        else:
            # Start manual at current slider speed
            self.fan_ctrl.set_manual(self.fan_speed)
            return {"success": True, "mode": "manual"}

    async def set_fan_speed(self, percent):
        """Set manual fan speed (0-100). Stops any active curve."""
        if not self.fan_ctrl:
            return {"success": False, "error": "No fan control backend"}
        self.fan_speed = max(0, min(100, int(percent)))
        self.fan_profile = "custom"
        # Stop curve if running
        if self.fan_curve_runner:
            await self.fan_curve_runner.stop()
            self.fan_curve_runner = None
        if self.fan_mode == "manual":
            self.fan_ctrl.set_manual(self.fan_speed)
        return {"success": True, "speed": self.fan_speed}

    async def set_fan_profile(self, name):
        """Set fan profile: 'silent', 'balanced', 'performance', 'custom'."""
        if not self.fan_ctrl:
            return {"success": False, "error": "No fan control backend"}
        self.fan_profile = name

        # Stop existing curve
        if self.fan_curve_runner:
            await self.fan_curve_runner.stop()
            self.fan_curve_runner = None

        if name == "custom":
            # Use manual slider value directly
            if self.fan_mode == "manual":
                self.fan_ctrl.set_manual(self.fan_speed)
            return {"success": True, "profile": "custom"}

        curve = PROFILES.get(name)
        if not curve:
            return {"success": False, "error": f"Unknown profile: {name}"}

        temp_sensor = find_temp_sensor()
        if not temp_sensor:
            return {"success": False, "error": "No temperature sensor found"}

        self.fan_mode = "manual"
        self.fan_curve_runner = FanCurveRunner(
            self.fan_ctrl, temp_sensor, curve, interval=2.0
        )
        loop = asyncio.get_event_loop()
        self.fan_curve_runner.start(loop)
        return {"success": True, "profile": name}
