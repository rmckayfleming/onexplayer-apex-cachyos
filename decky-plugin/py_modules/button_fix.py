"""Button fix for OneXPlayer Apex on Bazzite.

Patches HHD (Handheld Daemon) to add Apex device support with correct
button mappings and keyboard VID:PID. Requires ostree unlock + HHD restart.
"""

import glob
import logging
import os
import re
import subprocess

logger = logging.getLogger("OXP-ButtonFix")

# Patch content for const.py — Apex button mappings
APEX_MAPPINGS_BLOCK = '''
# Apex-specific: Home button sends KEY_G instead of KEY_D
APEX_BTN_MAPPINGS = {
    B("KEY_VOLUMEUP"): "key_volumeup",
    B("KEY_VOLUMEDOWN"): "key_volumedown",
    # Turbo Button: KEY_LEFTCTRL + KEY_LEFTALT + KEY_LEFTMETA
    B("KEY_LEFTALT"): "share",
    # Home/Orange Button: KEY_G + KEY_LEFTMETA (Apex uses KEY_G, not KEY_D)
    B("KEY_G"): "mode",
    # KB Button: KEY_O + KEY_RIGHTCTRL + KEY_LEFTMETA
    B("KEY_O"): "keyboard",
}
'''

APEX_DEVICE_ENTRY = '''    "ONEXPLAYER APEX": {
        "name": "ONEXPLAYER APEX",
        **ONEX_DEFAULT_CONF,
        "protocol": "hid_v2",
        "apex_kbd": True,
    },'''


def _find_hhd_files():
    """Locate HHD oxp const.py and base.py."""
    const_file = "/usr/lib/python3.14/site-packages/hhd/device/oxp/const.py"
    base_file = "/usr/lib/python3.14/site-packages/hhd/device/oxp/base.py"
    if os.path.exists(const_file):
        return const_file, base_file
    # Search for the files
    results = glob.glob("/usr/lib/python3*/site-packages/hhd/device/oxp/const.py")
    if results:
        const_file = sorted(results)[0]
        base_file = const_file.replace("const.py", "base.py")
        return const_file, base_file
    return None, None


def is_applied():
    """Check if the Apex button fix is already applied."""
    const_file, base_file = _find_hhd_files()
    if not const_file or not base_file:
        return {"applied": False, "error": "HHD oxp files not found"}
    try:
        with open(const_file) as f:
            const_content = f.read()
        with open(base_file) as f:
            base_content = f.read()
        const_ok = "ONEXPLAYER APEX" in const_content and "APEX_BTN_MAPPINGS" in const_content
        base_ok = "APEX_BTN_MAPPINGS" in base_content
        return {"applied": const_ok and base_ok, "const_patched": const_ok, "base_patched": base_ok}
    except Exception as e:
        return {"applied": False, "error": str(e)}


def apply():
    """Apply the Apex button fix. Idempotent — safe to re-run."""
    status = is_applied()
    if status.get("applied"):
        return {"success": True, "message": "Already applied"}

    const_file, base_file = _find_hhd_files()
    if not const_file or not base_file:
        return {"success": False, "error": "HHD oxp files not found"}

    # Unlock immutable filesystem
    try:
        subprocess.run(
            ["ostree", "admin", "unlock", "--hotfix"],
            capture_output=True, timeout=30
        )
    except Exception as e:
        logger.warning(f"ostree unlock: {e} (may already be unlocked)")

    errors = []

    # Patch const.py
    if not status.get("const_patched"):
        try:
            _patch_const(const_file)
        except Exception as e:
            errors.append(f"const.py: {e}")

    # Patch base.py
    if not status.get("base_patched"):
        try:
            _patch_base(base_file)
        except Exception as e:
            errors.append(f"base.py: {e}")

    if errors:
        return {"success": False, "error": "; ".join(errors)}

    # Restart HHD
    try:
        subprocess.run(["systemctl", "restart", "hhd"], capture_output=True, timeout=30)
    except Exception as e:
        return {"success": True, "warning": f"Patched but HHD restart failed: {e}"}

    return {"success": True, "message": "Button fix applied and HHD restarted"}


def _patch_const(const_file):
    """Patch const.py to add Apex device entry and button mappings."""
    with open(const_file) as f:
        content = f.read()

    # Remove partial Apex entries from previous attempts
    content = re.sub(r'    "ONEXPLAYER APEX".*?\n(?:.*?\n)*?    \},?\n', '', content)

    # Add Apex button mappings before ONEX_DEFAULT_CONF
    marker = 'ONEX_DEFAULT_CONF = {'
    if marker in content and 'APEX_BTN_MAPPINGS' not in content:
        content = content.replace(marker, APEX_MAPPINGS_BLOCK + '\n' + marker)

    # Add Apex device entry
    if 'ONEXPLAYER APEX' not in content:
        f1_marker = '"ONEXPLAYER F1 EVA-02": OXP_F1_CONF,'
        if f1_marker in content:
            content = content.replace(f1_marker, f1_marker + '\n' + APEX_DEVICE_ENTRY)
        else:
            oxp2_marker = '    # OXP 2'
            if oxp2_marker in content:
                content = content.replace(
                    oxp2_marker,
                    '    # Apex\n' + APEX_DEVICE_ENTRY + '\n' + oxp2_marker
                )

    with open(const_file, 'w') as f:
        f.write(content)
    logger.info("const.py patched")


def _patch_base(base_file):
    """Patch base.py to use Apex keyboard VID:PID and button mappings."""
    with open(base_file) as f:
        content = f.read()

    if 'APEX_BTN_MAPPINGS' in content:
        return

    # Update import
    old_import = 'from .const import BTN_MAPPINGS, BTN_MAPPINGS_NONTURBO, DEFAULT_MAPPINGS'
    new_import = 'from .const import APEX_BTN_MAPPINGS, BTN_MAPPINGS, BTN_MAPPINGS_NONTURBO, DEFAULT_MAPPINGS'
    content = content.replace(old_import, new_import)

    # Patch turbo_loop keyboard device
    old_turbo = '''    d_kbd_1 = OxpAtKbd(
        vid=[KBD_VID],
        pid=[KBD_PID],
        required=False,
        grab=True,
        btn_map=BTN_MAPPINGS,
    )

    share_reboots = False
    last_controller_check = 0'''

    new_turbo = '''    if dconf.get("apex_kbd", False):
        d_kbd_1 = OxpAtKbd(
            vid=[X1_MINI_VID],
            pid=[X1_MINI_PID],
            required=False,
            grab=True,
            btn_map=APEX_BTN_MAPPINGS,
        )
    else:
        d_kbd_1 = OxpAtKbd(
            vid=[KBD_VID],
            pid=[KBD_PID],
            required=False,
            grab=True,
            btn_map=BTN_MAPPINGS,
        )

    share_reboots = False
    last_controller_check = 0'''

    content = content.replace(old_turbo, new_turbo)

    # Patch controller_loop keyboard device
    old_ctrl = '''    if turbo:
        # Switch buttons if turbo is enabled.
        # This only affects AOKZOE and OneXPlayer devices with
        # that button that have the nonturbo mapping as default
        mappings = BTN_MAPPINGS
    else:
        mappings = BTN_MAPPINGS_NONTURBO

    d_kbd_1 = OxpAtKbd(
        vid=[KBD_VID],
        pid=[KBD_PID],
        required=False,
        grab=True,
        btn_map=mappings,
    )'''

    new_ctrl = '''    if turbo:
        # Switch buttons if turbo is enabled.
        # This only affects AOKZOE and OneXPlayer devices with
        # that button that have the nonturbo mapping as default
        mappings = BTN_MAPPINGS
    else:
        mappings = BTN_MAPPINGS_NONTURBO

    if dconf.get("apex_kbd", False):
        d_kbd_1 = OxpAtKbd(
            vid=[X1_MINI_VID],
            pid=[X1_MINI_PID],
            required=False,
            grab=True,
            btn_map=APEX_BTN_MAPPINGS,
        )
    else:
        d_kbd_1 = OxpAtKbd(
            vid=[KBD_VID],
            pid=[KBD_PID],
            required=False,
            grab=True,
            btn_map=mappings,
        )'''

    content = content.replace(old_ctrl, new_ctrl)

    with open(base_file, 'w') as f:
        f.write(content)
    logger.info("base.py patched")
