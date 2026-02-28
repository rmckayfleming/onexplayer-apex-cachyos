#!/usr/bin/env bash
# fix-buttons.sh — Add OneXPlayer Apex to HHD device config
#
# The Apex isn't in HHD's device list. This script patches HHD to:
# 1. Add the Apex as a known device with hid_v2 protocol
# 2. Use the correct keyboard device (1a86:fe00 instead of AT keyboard)
# 3. Map KEY_G to Home button (Apex uses KEY_G, not KEY_D like F1)
#
# Button mapping on the Apex (from event8 / HID 1a86:fe00):
#   KB button:    KEY_LEFTCTRL + KEY_LEFTMETA + KEY_O  → "keyboard"
#   Turbo button: KEY_LEFTCTRL + KEY_LEFTALT + KEY_LEFTMETA → "share"
#   Home button:  KEY_LEFTMETA + KEY_G → "mode"
#
# Note: This fix is lost on Bazzite updates. File upstream issue at:
# https://github.com/hhd-dev/hhd/issues

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (sudo)."
    exit 1
fi

# Find HHD files
CONST_FILE="/usr/lib/python3.14/site-packages/hhd/device/oxp/const.py"
BASE_FILE="/usr/lib/python3.14/site-packages/hhd/device/oxp/base.py"

if [[ ! -f "$CONST_FILE" ]]; then
    CONST_FILE=$(find /usr/lib/python3* -path "*/hhd/device/oxp/const.py" 2>/dev/null | head -1)
    BASE_FILE=$(find /usr/lib/python3* -path "*/hhd/device/oxp/base.py" 2>/dev/null | head -1)
    if [[ -z "$CONST_FILE" || -z "$BASE_FILE" ]]; then
        error "Could not find HHD oxp files"
        exit 1
    fi
fi

info "HHD const: $CONST_FILE"
info "HHD base:  $BASE_FILE"

# Unlock immutable filesystem
info "Unlocking filesystem for hotfix..."
if ! ostree admin unlock --hotfix 2>/dev/null; then
    warn "Filesystem unlock failed or already unlocked. Continuing..."
fi

# Patch const.py
info "Patching const.py..."
python3 -c "
const_file = '$CONST_FILE'

with open(const_file) as f:
    content = f.read()

if 'ONEXPLAYER APEX' in content and 'APEX_BTN_MAPPINGS' in content:
    print('Already patched')
    exit(0)

# Remove any partial Apex entry from previous attempts
import re
content = re.sub(r'    \"ONEXPLAYER APEX\".*?\n(?:.*?\n)*?    \},?\n', '', content)

# Add Apex button mappings before ONEX_DEFAULT_CONF
apex_mappings = '''
# Apex-specific: Home button sends KEY_G instead of KEY_D
APEX_BTN_MAPPINGS = {
    B(\"KEY_VOLUMEUP\"): \"key_volumeup\",
    B(\"KEY_VOLUMEDOWN\"): \"key_volumedown\",
    # Turbo Button: KEY_LEFTCTRL + KEY_LEFTALT + KEY_LEFTMETA
    B(\"KEY_LEFTALT\"): \"share\",
    # Home/Orange Button: KEY_G + KEY_LEFTMETA (Apex uses KEY_G, not KEY_D)
    B(\"KEY_G\"): \"mode\",
    # KB Button: KEY_O + KEY_RIGHTCTRL + KEY_LEFTMETA
    B(\"KEY_O\"): \"keyboard\",
}
'''

marker = 'ONEX_DEFAULT_CONF = {'
if marker in content and 'APEX_BTN_MAPPINGS' not in content:
    content = content.replace(marker, apex_mappings + '\n' + marker)

# Add Apex device entry
apex_entry = '''    \"ONEXPLAYER APEX\": {
        \"name\": \"ONEXPLAYER APEX\",
        **ONEX_DEFAULT_CONF,
        \"protocol\": \"hid_v2\",
        \"apex_kbd\": True,
    },'''

f1_marker = '\"ONEXPLAYER F1 EVA-02\": OXP_F1_CONF,'
if f1_marker in content and 'ONEXPLAYER APEX' not in content:
    content = content.replace(f1_marker, f1_marker + '\n' + apex_entry)
elif 'ONEXPLAYER APEX' not in content:
    oxp2_marker = '    # OXP 2'
    if oxp2_marker in content:
        content = content.replace(oxp2_marker, '    # Apex\n' + apex_entry + '\n' + oxp2_marker)

with open(const_file, 'w') as f:
    f.write(content)
print('const.py patched')
"

# Patch base.py
info "Patching base.py..."
python3 -c "
base_file = '$BASE_FILE'

with open(base_file) as f:
    content = f.read()

if 'APEX_BTN_MAPPINGS' in content:
    print('Already patched')
    exit(0)

# Update import
old_import = 'from .const import BTN_MAPPINGS, BTN_MAPPINGS_NONTURBO, DEFAULT_MAPPINGS'
new_import = 'from .const import APEX_BTN_MAPPINGS, BTN_MAPPINGS, BTN_MAPPINGS_NONTURBO, DEFAULT_MAPPINGS'
content = content.replace(old_import, new_import)

# Patch turbo_loop keyboard device (first occurrence)
old_turbo = '''    d_kbd_1 = OxpAtKbd(
        vid=[KBD_VID],
        pid=[KBD_PID],
        required=False,
        grab=True,
        btn_map=BTN_MAPPINGS,
    )

    share_reboots = False
    last_controller_check = 0'''

new_turbo = '''    if dconf.get(\"apex_kbd\", False):
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

# Patch controller_loop keyboard device (second occurrence)
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

    if dconf.get(\"apex_kbd\", False):
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
print('base.py patched')
"

# Restart HHD
info "Restarting HHD..."
systemctl restart hhd
sleep 5

# Verify
if journalctl -u hhd --since "7 seconds ago" --no-pager | grep -q "Emulated controller launched"; then
    info "HHD launched successfully! Buttons should now work."
else
    warn "Check HHD logs: journalctl -u hhd --no-pager | tail -30"
fi

echo ""
info "This fix will be lost on the next Bazzite update."
info "To make permanent, submit upstream: https://github.com/hhd-dev/hhd/issues"
