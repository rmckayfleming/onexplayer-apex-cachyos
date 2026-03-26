# CachyOS Adaptation Plan

This repo was built for the OneXPlayer Apex on **Bazzite** (Fedora Atomic + HHD + ostree). We're adapting it for **CachyOS Deckify** (Arch-based + InputPlumber + pacman).

## System Inventory (current state)

| Component | CachyOS | Bazzite (original) |
|-----------|---------|-------------------|
| **Package manager** | pacman (mutable filesystem) | rpm-ostree (immutable) |
| **Gamepad daemon** | InputPlumber 0.75.1 | HHD 4.1.5 |
| **Kernel** | 6.19.6-2-cachyos-deckify | 6.17.7-ba25/ba28 |
| **Plugin system** | Decky (to be installed) | Decky |
| **Audio** | PipeWire | PipeWire |
| **Init** | systemd | systemd |
| **Filesystem** | Writable (standard Arch) | Immutable (ostree) |
| **Kernel headers** | Available | Requires kernel-devel RPM |
| **DMI product_name** | `ONEXPLAYER APEX` | `ONEXPLAYER APEX` |
| **DMI sys_vendor** | `ONE-NETBOOK` | `ONE-NETBOOK` |

## What Already Works / Doesn't Need Porting

- **Speaker DSP** (`speaker_dsp.py`): Writes to `~/.config/pipewire/` — purely PipeWire, distro-agnostic. No changes needed.
- **Resume Recovery** (`resume_fix.py`): Creates systemd service + bash script. Distro-agnostic. No changes needed.
- **Back Paddle firmware remap** (`back_paddle.py`): Talks directly to HID device. Distro-agnostic. No changes needed to the remap itself.
- **Home Button HID monitor** (`home_button.py`): Reads raw HID. Distro-agnostic in principle, but launches `hhd-ui` which doesn't exist here.

## What Needs Changing

### Phase 1: Remove Bazzite-isms (required for anything to work)

These are blocking changes — the plugin will crash or fail on CachyOS without them.

#### 1.1 Remove ostree unlock calls
**Files**: `button_fix.py`, `sleep_enable.py`
**Why**: CachyOS has a writable filesystem. `ostree admin unlock --hotfix` doesn't exist.
**Change**: Remove `_unlock_filesystem()` and `_is_filesystem_writable()` checks. On CachyOS, `/usr/lib/` is always writable (but gets overwritten on package updates, same as Bazzite updates).

#### 1.2 Replace rpm-ostree kargs with kernel cmdline editing
**Files**: `sleep_fix.py`
**Why**: CachyOS uses systemd-boot or GRUB, not rpm-ostree.
**Change**: Replace `rpm-ostree kargs --append/--delete` with writing to `/etc/kernel/cmdline` (for systemd-boot, which CachyOS Deckify uses) and running `sudo reinstall-kernels` or `bootctl update`. Current cmdline is:
```
initrd=\initramfs-linux-cachyos-deckify.img root=UUID=... rw rootflags=subvol=/@ zswap.enabled=0 nowatchdog quiet splash fbcon=vc:2-6
```
Note: `s2idle` is already the default (`[s2idle]` in `/sys/power/mem_sleep`), and `amd_iommu=off` is NOT currently set. Need to verify if it's still required on kernel 6.19.

#### 1.3 Remove fw-fanctrl-suspend fix
**Files**: `sleep_enable.py`
**Why**: CachyOS doesn't ship the Framework Laptop `fw-fanctrl-suspend` script. This fix is unnecessary.
**Change**: Remove or skip the script neutralization. Keep the fingerprint reader udev rule (that's useful on any distro).

#### 1.4 Remove HHD button fix entirely
**Files**: `button_fix.py`, `hhd_patches/`
**Why**: CachyOS uses InputPlumber, not HHD. There's no HHD to patch.
**Change**: Replace with InputPlumber device profile (see Phase 2).

### Phase 2: InputPlumber Integration (gamepad + buttons)

InputPlumber 0.75.1 is installed but has **no device profile for the Apex**. The existing profiles cover the OnexFly (`50-onexplayer_onexfly.yaml`) and older models, but nothing matches `ONEXPLAYER APEX`. The closest capability map is `oxp7` (Type 7, used by OnexFly).

#### 2.1 Create Apex device profile
**File**: New file, e.g. `inputplumber/50-onexplayer_apex.yaml`
**What**: A CompositeDevice config that matches DMI `product_name: ONEXPLAYER APEX` / `sys_vendor: ONE-NETBOOK` and defines source devices:
- Xbox 360 gamepad (`045e:028e`)
- Vendor HID keyboard (`1a86:fe00` / `1a2c:b001`)
- Possibly IMU if present

**Reference**: Base on `50-onexplayer_onexfly.yaml` but with correct `phys_path` for the Apex's USB topology (controller is on `usb-0000:65:00.4`).

**Install location**: `/usr/share/inputplumber/devices/50-onexplayer_apex.yaml` (or `/etc/inputplumber/devices/` for user overrides).

#### 2.2 Create/update Apex capability map
**File**: New or extend existing, e.g. `inputplumber/onexplayer_apex.yaml`
**What**: Map the Apex's special buttons and paddles:
- Home/Turbo button: `LCtrl+LAlt+LGUI` -> QuickAccess (already in oxp7)
- Orange button: `LGUI+D` (short) / `LGUI+G` (long) (already in oxp7)
- **Back paddles M1/M2**: After firmware remap, these send F13/F14 as keyboard events. Need to map `KeyF13` -> `LeftPaddle1` and `KeyF14` -> `RightPaddle1` (or `LeftPaddle2`/`RightPaddle2`)

The existing `oxp7` map is close but only has M2 mapped (to RightPaddle1 via Guide button, which is wrong for the Apex). We need a proper Apex-specific map.

#### 2.3 Back paddle activation service
**What**: The firmware remap persists, but B2 "report mode" must be re-activated each boot. Create a systemd service that runs `back_paddle.py`'s `setup_paddles()` at boot.
**Dependencies**: Needs to run after USB devices are up but before InputPlumber starts managing them. Or just run on a timer/retry since the device may not be ready immediately.

#### 2.4 Adapt plugin backend for InputPlumber
**Files**: `main.py`, `button_fix.py` (or new `inputplumber_fix.py`)
**What**: Replace HHD patching RPC calls with:
- Installing/removing the InputPlumber device profile
- Restarting InputPlumber service (`systemctl restart inputplumber`)
- Status checks (is profile installed? is InputPlumber running?)

### Phase 3: oxpec Kernel Module

#### 3.1 Build oxpec.ko for CachyOS kernel
**Source**: `decky-plugin/py_modules/oxpec/build/`
**What**: Build against `6.19.6-2-cachyos-deckify` headers (already installed at `/usr/lib/modules/6.19.6-2-cachyos-deckify/build/`).
```bash
cd decky-plugin/py_modules/oxpec/build/
make KDIR=/usr/lib/modules/$(uname -r)/build
```
**Output**: Place `oxpec.ko` in `decky-plugin/py_modules/oxpec/6.19.6-2-cachyos-deckify/`

#### 3.2 DKMS package (optional, recommended)
**Why**: CachyOS kernel updates frequently (rolling release). Instead of bundling `.ko` files per kernel, set up DKMS so the module auto-rebuilds on kernel updates.
**What**: Create a `dkms.conf` + install script that registers oxpec with DKMS. Then `oxpec_loader.py` just does `modprobe oxpec` and it always works.

#### 3.3 Update oxpec_loader.py
**What**: Remove SELinux-specific code (`chcon` calls). CachyOS doesn't use SELinux. Add DKMS awareness if we go that route.

### Phase 4: Sleep Investigation

#### 4.1 Test s2idle without amd_iommu=off
**Why**: Kernel 6.19 may have better Strix Halo sleep support than 6.17. The `amd_iommu=off` karg was required on Bazzite's 6.17 kernel — it may not be needed anymore.
**Test**: Try sleep without the karg first. If it works, we can skip the kargs modification entirely.

#### 4.2 Adapt kargs management if needed
**What**: If kargs are still needed, use CachyOS's bootloader config:
- Check if using systemd-boot or GRUB
- For systemd-boot: edit `/etc/kernel/cmdline` and run `reinstall-kernels`
- For GRUB: edit `/etc/default/grub` `GRUB_CMDLINE_LINUX_DEFAULT` and run `grub-mkconfig`

### Phase 5: Plugin UI Updates

#### 5.1 Update frontend text/labels
**Files**: `src/FixesSection.tsx`, `src/index.tsx`
**What**:
- Change "Bazzite" references to "CachyOS"
- Remove HHD-specific UI (button fix toggle)
- Add InputPlumber profile install toggle
- Remove "ostree unlock" status messages
- Update sleep fix UI to reflect CachyOS bootloader

#### 5.2 Remove dead code
**Files**: Various
**What**: Remove `sleep_enable.py`'s fw-fanctrl code, remove `button_fix.py`'s ostree code, clean up references to rpm-ostree.

#### 5.3 Update plugin metadata
**Files**: `plugin.json`, `package.json`, `README.md`
**What**: Update names, descriptions, and docs to reflect CachyOS target.

### Phase 6: CI/CD Updates

#### 6.1 Update kernel check workflow
**File**: `.github/workflows/oxpec-kernel-check.yml`
**What**: Currently checks Bazzite kernel releases. Update to check CachyOS kernel updates (or remove if using DKMS).

## Suggested Implementation Order

1. ~~**Phase 3.1** — Build oxpec.ko~~ DONE — built for `6.19.6-2-cachyos-deckify`, placed in `decky-plugin/py_modules/oxpec/6.19.6-2-cachyos-deckify/oxpec.ko`
2. ~~**Phase 2.1 + 2.2** — InputPlumber device profile + capability map~~ DONE — created `inputplumber/50-onexplayer_apex.yaml` and `inputplumber/onexplayer_apex.yaml`
3. ~~**Phase 1.1-1.4** — Remove Bazzite-isms from Python backend~~ DONE — rewrote `button_fix.py` (InputPlumber), `sleep_fix.py` (systemd-boot), `sleep_enable.py` (udev only), `oxpec_loader.py` (no SELinux), `home_button.py` (no HHD), `main.py` (InputPlumber references), `back_paddle.py` (updated docs), `speaker_dsp.py` (updated docs)
4. **Phase 2.3** — Back paddle boot service (TODO)
5. **Phase 4** — Sleep testing (TODO — test if `amd_iommu=off` needed on kernel 6.19)
6. **Phase 2.4 + 5** — Plugin UI adaptation (TODO)
7. **Phase 3.2** — DKMS (nice-to-have for rolling kernel updates)
8. **Phase 6** — CI/CD

## Open Questions

- [ ] Does InputPlumber already partially work with the Apex without a profile? (try `systemctl start inputplumber` and test)
- [ ] Is `amd_iommu=off` still needed on kernel 6.19?
- [ ] Does oxpec.ko build cleanly against 6.19 headers? (the driver was written for 6.17)
- [ ] Does CachyOS Deckify use systemd-boot or GRUB? (affects kargs management)
- [ ] Should we contribute the Apex profile upstream to InputPlumber?
- [ ] Is Decky Loader compatible with CachyOS out of the box, or does it need patches?
