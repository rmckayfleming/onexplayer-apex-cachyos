# Linux Gaming OS Research: OneXFly Apex Compatibility

**Date:** 2026-02-27
**Device:** OneXPlayer OneXFly Apex (AMD Ryzen AI Max+ 395 "Strix Halo")
**Current OS:** Windows

---

## Table of Contents

1. [Device Overview](#device-overview)
2. [Linux Gaming OS Options](#linux-gaming-os-options)
3. [Bazzite (Recommended)](#bazzite-recommended)
4. [SteamOS](#steamos)
5. [Other Options](#other-options-chimeraos-nobara-cachyos)
6. [Key Concern Areas](#key-concern-areas)
7. [Strix Halo Linux Maturity](#strix-halo-linux-maturity)
8. [Verdict & Recommendation](#verdict--recommendation)

---

## Device Overview

The **OneXFly Apex** is OneXPlayer's flagship Strix Halo handheld:

| Spec | Details |
|------|---------|
| **APU** | AMD Ryzen AI Max+ 395 (16C/32T, up to 5.1 GHz) |
| **GPU** | Radeon 8060S (RDNA 3.5, 40 CUs) — or 8050S (32 CUs) on lower SKU |
| **RAM** | Up to 128GB LPDDR5x-8000 (quad-channel), up to 96GB available as VRAM |
| **Display** | 8" 1920x1200 (16:10), 120Hz, VRR, **native landscape** |
| **TDP** | Up to 80W air-cooled / 120W with optional liquid cooling |
| **Battery** | 85Wh swappable module |
| **Storage** | Dual SSD (M.2 2280 PCIe 4.0 + external Mini SSD) |
| **Ports** | USB-C 4.0, USB-C 3.2, USB-A 3.2, microSD |
| **Controls** | Asymmetric sticks, Hall-effect adjustable 2-stage triggers |
| **Pricing** | $1,699–$2,539 depending on config |

The native landscape display is a big plus for Linux — many other OneXPlayer/handheld devices use portrait panels rotated in software, which causes orientation headaches on Linux. The Apex avoids this entirely.

---

## Linux Gaming OS Options

### Quick Comparison

| Feature | Bazzite | SteamOS | ChimeraOS | Nobara |
|---------|---------|---------|-----------|--------|
| **Base** | Fedora Atomic | Arch (Valve fork) | Arch | Fedora |
| **Handheld focus** | Excellent | Steam Deck first | Good | Desktop first |
| **OneXPlayer support** | Official (w/ OXP collaboration) | Unofficial/unsupported | Limited | Manual |
| **GPU support** | AMD, Nvidia, Intel | AMD only | AMD (Nvidia partial) | AMD, Nvidia |
| **Update model** | Atomic (rollback) | Valve-controlled | Locked-down image | Traditional (mutable) |
| **Desktop mode** | Full KDE/GNOME | Limited KDE | Minimal GNOME | Full GNOME |
| **Non-Steam stores** | Yes (Epic, GOG, etc.) | Steam only | Steam-focused | Yes |
| **HHD (Handheld Daemon)** | Pre-installed | Not included | Separate | Manual |

---

## Bazzite (Recommended)

**Website:** https://bazzite.gg
**Docs:** https://docs.bazzite.gg

Bazzite is the leading community Linux gaming OS for non-Steam Deck handhelds. It's built on Fedora Atomic and provides a SteamOS-like gaming mode experience with much broader hardware support.

### OneXPlayer Support Status

Bazzite officially added OneXPlayer support in October 2024, covering:
- OneXPlayer X1 (AMD), X1 Mini
- OneXFly variants
- Mini Pro

**The OneXFly Apex is NOT explicitly listed yet** (it's too new, shipping completed ~Jan 2026). However:

1. OneXPlayer is **actively collaborating with Bazzite** — they stated: "we are proud to state that OneXPlayer will be helping us on that journey"
2. HHD (Handheld Daemon) aims to support new models from supported manufacturers as they release
3. The Apex uses the same controller/input architecture as other OneXFly devices
4. The `oxp-sensors` kernel driver provides the low-level interface for OneXPlayer hardware

### What Works on OneXPlayer + Bazzite (established models)

- **Controller emulation** via Steam Controller mode (recommended)
- **Gyro support**
- **RGB control**
- **Back buttons + turbo button**
- **Fan curves** (added Jan 2025 update)
- **Charge limiting**
- **TDP control** via HHD overlay, SimpleDeckyTDP, or PowerControl
- **Sleep/suspend** — improved with modern standby patches (Jan 2025):
  - Properly shuts down built-in controller before suspend
  - Turns off RGB and display during sleep transition
  - Fixes the "ugly stale frozen image" during hibernate
  - Device-specific sleep light pulsing

### Known Issues on OneXFly devices (may or may not apply to Apex)

| Issue | Details | Status |
|-------|---------|--------|
| **Screen orientation** | Earlier OneXFly models had upside-down display in Game Mode | Apex has native landscape — likely not an issue |
| **Brightness control** | F1 Pro had screen-off bug when adjusting brightness in Game Mode | Unknown on Apex |
| **Audio** | Some OneXFly models had no internal speaker audio | Unknown on Apex |
| **VRAM allocation** | VRAM stuck at 4GB with no BIOS option (workaround: set in Windows first) | May apply |
| **Battery shutdowns** | F1 Pro reported unexpected shutdown at 20% | Unknown on Apex |
| **Button mapping** | Home/Turbo/Keyboard buttons initially non-functional | Later fixed in OXP support update |

### Bazzite Strengths

- **Atomic/immutable OS** — every update preserves the previous version; rollback at boot if something breaks
- **Broad GPU support** — AMD, Nvidia, Intel
- **Full desktop mode** — KDE Plasma or GNOME for non-gaming tasks
- **Multi-store support** — Steam, Epic, GOG, emulators, Android apps (via Waydroid)
- **BTRFS** with deduplication and compression
- **Auto-mounting** for drives and SD cards
- **MicroSD game library sharing** across multiple Bazzite installs
- **System76 scheduler** for improved responsiveness
- **Gamescope** with HDR and VRR improvements

### Bazzite Weaknesses

- Not Valve-backed — community project (though very active, ~Fedora 43 base as of Feb 2026)
- Some features lag behind for newer OneXPlayer models
- Fan curve/TDP granularity still being improved for some devices
- "Bazzite isn't SteamOS" — some Steam features may behave slightly differently

---

## SteamOS

**Website:** https://store.steampowered.com/steamos/

### Current State (as of Feb 2026)

SteamOS has been expanding beyond Steam Deck:

| Milestone | Date | Details |
|-----------|------|---------|
| Lenovo Legion Go S announced as first official 3rd-party device | Jan 2025 (CES) | |
| SteamOS 3.6.19 | Early 2025 | Extended input support for ROG Ally keys |
| SteamOS 3.7.0 Preview | Mar 2025 | "Beginnings of support for non-Steam Deck handhelds" |
| SteamOS 3.7.5 Beta | May 2025 | ASUS + Lenovo handheld support starts |
| SteamOS 3.7.8 Stable | May 2025 | Compatible with any AMD handheld (unofficial) |

### OneXPlayer + SteamOS: Not Good

PC Gamer's hardware editor tested SteamOS on multiple OneXPlayer devices (OneXFly, X1, OneXFly F1 Pro):

> "Every time I tried it, it was a buggy experience, if not completely unworkable."

**Only the Steam Deck and Lenovo Legion Go S run SteamOS "pretty flawlessly."**

Specific issues reported:
- Installer hangs (e.g., stuck at "started Wireless service" or "Finished boot registration")
- Inverted landscape display in Game Mode
- No official OneXPlayer partnership with Valve (unlike Lenovo)
- OneXPlayer-specific hardware features (fan control, TDP, RGB) not supported

### Verdict on SteamOS for OneXFly Apex

**Not recommended currently.** Valve hasn't partnered with OneXPlayer, and even on established OXP models the experience is buggy/unworkable. The Apex being a brand-new Strix Halo device makes it even less likely to work well.

---

## Other Options: ChimeraOS, Nobara, CachyOS

### ChimeraOS
- Arch-based, console-first experience
- Uses gamescope compositor (same as SteamOS)
- Supports Decky Loader plugins
- **Limited handheld support** compared to Bazzite
- No explicit OneXPlayer support
- Joined the Open Gaming Collective (OGC) in Jan 2026

### Nobara
- Fedora-based, maintained by GloriousEggroll (Proton-GE creator)
- Pre-installed Steam, Proton-GE, Lutris, OBS
- Kernel tweaks for low latency and high FPS
- **Traditional (mutable) distro** — no atomic rollback
- Better suited for **desktop gaming** than handheld
- Also part of the Open Gaming Collective

### CachyOS Handheld Edition
- Arch-based with performance-optimized kernel
- Growing handheld support
- Uses InputPlumber (same input framework as SteamOS)
- Less community documentation than Bazzite

### The Open Gaming Collective (OGC) — Jan 2026

Major development: Bazzite, Nobara, ChimeraOS, Playtron, and others formed the OGC to share:
- Gamescope patches
- Hardware drivers
- Input systems (InputPlumber becoming the shared standard)
- This should accelerate hardware support across all distros

---

## Key Concern Areas

### 1. Sleep / Suspend

**Status: Improving, but still a risk area**

**Bazzite-specific improvements (Jan 2025):**
- Modern standby patches that enter Windows-like standby states before suspend
- Properly shuts down controller and RGB before sleep
- Device-specific sleep light pulsing (including OneXPlayer)
- Hibernation support added

**Strix Halo / amdgpu concerns:**
- The amdgpu driver has known suspend/resume bugs related to VRAM eviction — if there isn't enough free RAM to store all VRAM in use, the system can OOM and crash instead of swapping to disk
- The Apex with up to 128GB RAM and large VRAM allocation makes this potentially relevant
- MES firmware (v0.80) on Strix Halo (gfx1151) has known hang issues during compute wave store and resume — workaround: `amdgpu.cwsr_enable=0`
- Using the **latest kernel** (6.15+) is strongly recommended
- Kernels older than 6.18.4 have a specific bug causing stability issues on gfx1151

**Bottom line:** Sleep is likely to work on Bazzite but may require kernel parameter tweaks. It won't be as seamless as Windows out of the box. The Bazzite team specifically addresses OneXPlayer sleep quirks (e.g., OXP devices that "wake up after 5% battery loss and pretend to overheat" on Windows).

### 2. Controller Mappings

**Status: Good on Bazzite, via HHD**

- **Handheld Daemon (HHD)** comes pre-installed on Bazzite and provides:
  - Xbox controller emulation (recommended for OneXPlayer)
  - DualSense emulation (with gyro)
  - Back button remapping
  - Turbo button support
  - Per-game power profiles
- Steam Controller emulation is the recommended mode
- Desktop Mode may need manual controller layout setup in Steam settings
- The Apex's Hall-effect 2-stage triggers should work as standard analog triggers, though the mechanical toggle switch behavior is unknown on Linux

**Potential issue:** The Apex is a new device — HHD may need a config update to recognize it. This is usually quick once someone reports it.

### 3. Fan Curve Control

**Status: Supported for OneXPlayer on Bazzite, but Apex-specific unknown**

- Fan curves for OneXPlayer were added to Bazzite in the Jan 2025 update
- The `oxp-sensors` kernel driver provides the hardware interface
- Three tools available for fan/TDP control:
  1. **HHD overlay** — TDP controls (fan may vary by device)
  2. **SimpleDeckyTDP** — TDP, GPU, Power Governor
  3. **PowerControl** — TDP, GPU, and fan controls on select devices
- The Apex's unique cooling system (optional liquid cooling at 120W) is likely not yet supported — the liquid cooling accessory may not have Linux drivers

**Bottom line:** Basic fan control should work via `oxp-sensors` if the Apex uses the same EC interface as other OXP devices. The liquid cooling system is a wildcard.

### 4. TDP Control

**Status: Partially supported**

- HHD overlay and SimpleDeckyTDP provide TDP adjustment
- The Apex supports 15W–80W (air) or up to 120W (liquid) — Linux tools may not expose the full range
- Some OneXPlayer models (like X1 Air) still lack granular TDP control on Bazzite
- This is actively being worked on

### 5. Display

**Status: Should be fine**

- The Apex has a **native landscape** 8" 1920x1200 display — this avoids the portrait-panel rotation issues that plague many other handhelds on Linux
- 120Hz + VRR should work with Gamescope (Bazzite's compositor)
- HDR support is improving in Gamescope builds

---

## Strix Halo Linux Maturity

The Ryzen AI Max+ 395 / Radeon 8060S is a very new platform (launched Feb 2025). Linux support has been evolving rapidly:

### What works well
- **Gaming performance:** Phoronix benchmarks show Ubuntu 25.04 **matching or beating Windows 11** on Strix Halo:
  - Vulkan gaming: up to 59% better FPS on Linux in some tests
  - Blender rendering: ~25% faster on Linux
  - General compute: competitive across the board
- **GPU driver:** amdgpu with RDNA 3.5 (gfx1151) works well for gaming with Mesa 25.0+
- **Display output:** No display issues or GPU hangs reported in normal gaming use
- **NPU:** XDNA driver available for AI workloads (though firmware compatibility is still evolving)

### What has issues
- **Kernel version sensitivity:** Must use kernel 6.15+ for good performance; 6.18.4+ recommended for stability
- **MES firmware bugs:** Compute wave store/resume can cause GPU hangs (workaround available)
- **ROCm (GPU compute):** Still maturing for consumer APUs — relevant if you want to run local AI models
- **Combined AI + streaming workloads:** Can cause GPU hangs (workaround: use software video encoding)
- **Firmware pinning:** Don't use linux-firmware-20251125 — it breaks ROCm on Strix Halo

### Performance comparison (Phoronix, Strix Halo)

| Benchmark | Linux vs Windows |
|-----------|-----------------|
| FurMark Vulkan | Linux +59% |
| Unvanquished | Linux +8% to +36% |
| Blender | Linux +25% |
| LZ4 compression | Windows +50% |
| Unigine Heaven | Windows +27% |
| Image encoding | Windows +10% |

---

## Verdict & Recommendation

### Should you switch from Windows?

**Not yet — but it's getting close. Here's a phased approach:**

### Phase 1: Try Bazzite from USB/External SSD (Low Risk)

OneXPlayer's official store even has a guide for [installing Bazzite on an external SSD](https://onexplayerstore.com/blogs/weekly-blog/how-to-install-bazzite-os-on-onexplayer-f1-pro-external-ssd). This lets you:

1. Keep Windows intact on internal storage
2. Boot Bazzite from external SSD to test everything
3. Verify: sleep, controllers, fan curves, audio, display
4. No risk to your Windows install

### Phase 2: Evaluate the experience

Check these critical items before committing:
- [ ] Does sleep/resume work reliably?
- [ ] Are controller mappings correct (sticks, triggers, back buttons, gyro)?
- [ ] Does fan control work via HHD?
- [ ] Is TDP adjustable through the full range?
- [ ] Does audio work from internal speakers?
- [ ] Are all USB ports functional?
- [ ] Do your most-played games run well via Proton?
- [ ] Does the 120Hz VRR display work correctly in Gamescope?

### Phase 3: Dual-boot or full switch

If Phase 2 goes well, you can:
- Set up a dual-boot configuration
- Or fully replace Windows

### Risk Assessment

| Area | Risk Level | Notes |
|------|-----------|-------|
| **Gaming performance** | Low | Linux matches/beats Windows on Strix Halo |
| **Sleep/suspend** | Medium-High | Likely needs kernel params; amdgpu VRAM eviction bugs possible with high VRAM usage |
| **Controller** | Medium | HHD should work but Apex may need config addition |
| **Fan curves** | Medium | Works for established OXP models; Apex/liquid cooling unknown |
| **TDP control** | Medium | Partial support; full range may not be exposed |
| **Display** | Low | Native landscape avoids rotation issues; VRR should work |
| **Audio** | Medium | Some OneXFly models had issues |
| **Anti-cheat games** | High | Some games (Apex Legends, Fortnite, etc.) don't support Linux |
| **Liquid cooling** | High | Very unlikely to be supported on Linux currently |

### TL;DR

**Bazzite is the only viable Linux option for the OneXFly Apex right now.** SteamOS is buggy on OneXPlayer devices. ChimeraOS/Nobara have less handheld support. The good news is that OneXPlayer is actively collaborating with Bazzite, Strix Halo gaming performance is excellent on Linux, and the Apex's native landscape display avoids a major pain point. The bad news is the Apex is brand new and specific support (fan curves, liquid cooling, TDP range, sleep quirks) hasn't been confirmed yet.

---

## Update: Post-Install Findings (2026-02-27)

Bazzite has been installed on a partition alongside Windows. Confirmed issues on the device:

### Confirmed: Face Buttons Don't Work

The OneXFly Apex gamepad is too new for InputPlumber/HHD to have a device profile. The input daemon likely grabs the raw HID device exclusively but doesn't forward events because it can't match the Apex's DMI strings to a known profile. The fix is to create an InputPlumber device profile (YAML) or add the Apex to HHD's OXP device list. Full fix guide in `docs/onexplayer-apex-bazzite-guide.md § Section 2`.

### Confirmed: No Fan Control

The `oxpec` kernel driver has an upstream patch for Apex support (submitted Feb 23, 2026 by Antheas) but it hasn't landed in Bazzite's kernel yet. Workaround: direct EC register access via `ec_sys` module. A Decky plugin will be built for Game Mode fan control. Full implementation plan in `docs/onexplayer-apex-fan-control-plan.md`.

### Sleep: Status TBD

Multiple known Strix Halo suspend bugs exist (VPE timeout hang, MES firmware CWSR, VRAM eviction OOM). Kernel parameters `amdgpu.cwsr_enable=0 iommu=pt` should be applied immediately. Full workaround list in `docs/onexplayer-apex-bazzite-guide.md § Section 3`.

### HHD → InputPlumber Migration

Bazzite is migrating from HHD to InputPlumber as part of the Open Gaming Collective (OGC, formed Jan 29, 2026). InputPlumber uses the same input framework across SteamOS, ChimeraOS, Nobara, and others. This migration does NOT affect the fan control kernel driver or Decky plugin — those talk directly to hwmon sysfs / EC registers.

### Next Steps

1. Run first-boot diagnostics (see guide § Section 1)
2. Fix face buttons via InputPlumber device profile (see guide § Section 2)
3. Apply kernel parameters for sleep stability (see guide § Section 3)
4. Build fan control Decky plugin using Claude Code on the device (see `docs/onexplayer-apex-fan-control-plan.md`)

---

## Key Sources

- [Bazzite Official Site](https://bazzite.gg/)
- [Bazzite OneXPlayer Handheld Wiki](https://docs.bazzite.gg/Handheld_and_HTPC_edition/Handheld_Wiki/OneXPlayer_Handhelds/)
- [Bazzite vs SteamOS Comparison](https://docs.bazzite.gg/General/SteamOS_Comparison/)
- [Bazzite Jan 2025 Update (Sleep Fixes, Fan Curves)](https://universal-blue.discourse.group/t/bazzite-update-happy-new-year-sleep-fixes-smoother-updates-bootc-fan-curves-gpd-more-devices/6200)
- [Bazzite OneXPlayer Support Announcement](https://universal-blue.discourse.group/t/bazzite-update-onexplayer-support-ally-goodies/4517)
- [Handheld Daemon (HHD)](https://github.com/hhd-dev/hhd)
- [PC Gamer: SteamOS on Handhelds Still Buggy](https://www.pcgamer.com/software/linux/2025-might-have-been-the-year-for-linux-gaming-but-theres-still-a-way-to-go-until-i-switch-from-windows/)
- [GamingOnLinux: Bazzite Adds OneXPlayer Support](https://www.gamingonlinux.com/2024/10/steamos-alternative-bazzite-adds-support-for-onexplayer-plus-improvements-for-rog-ally/)
- [Phoronix: Strix Halo Linux vs Windows](https://www.phoronix.com/review/amd-strix-halo-windows-linux)
- [Phoronix: Radeon 8060S Linux Performance](https://www.phoronix.com/review/amd-radeon-8060s-linux)
- [Phoronix: Linux 6.15 + Mesa 25.2 Strix Halo Gains](https://www.phoronix.com/news/Linux-6.15-Mesa-25.2-Strix-Halo)
- [SteamOS 3.7.0 Non-Deck Handheld Support](https://www.gamingonlinux.com/2025/03/steamos-3-7-0-preview-brings-the-beginnings-of-support-for-non-steam-deck-handhelds/)
- [SteamOS 3.7.5 Beta Updates](https://steamdeckhq.com/news/steamos-3-7-5-hits-beta-with-tons-of-updates/)
- [Open Gaming Collective Announcement](https://www.pcgamer.com/software/linux/a-whole-bunch-of-different-linux-gaming-distros-are-teaming-up-to-improve-the-open-source-gaming-ecosystem/)
- [Bazzite Wikipedia](https://en.wikipedia.org/wiki/Bazzite_(operating_system))
- [How-To Geek: Bazzite vs SteamOS](https://www.howtogeek.com/reasons-to-replace-steamos-with-bazzite-and-reasons-not-to/)
- [IndieKings: Best OS for Handheld Gaming 2025](https://www.indiekings.com/2025/03/best-operating-systems-for-handheld.html)
- [InputPlumber GitHub](https://github.com/ShadowBlip/InputPlumber)
- [VPE Idle Timeout Patch (amd-gfx)](https://www.mail-archive.com/amd-gfx@lists.freedesktop.org/msg127724.html)
- [ROCm MES Firmware Hang (CWSR)](https://github.com/ROCm/ROCm/issues/5590)
- [ROCm GPU Hang with AI + Video Encoding](https://github.com/ROCm/ROCm/issues/5665)
- [Bazzite Strix Halo Regression](https://github.com/ublue-os/bazzite/issues/3818)
- [Bazzite Memory Bandwidth Bug](https://github.com/ublue-os/bazzite/issues/3317)
- [OneXFly Button Issues](https://github.com/ublue-os/bazzite/issues/1635)
- [amdgpu Sleep-Wake Hang Blog](https://nyanpasu64.gitlab.io/blog/amdgpu-sleep-wake-hang/)
- [Decky Plugin Template](https://github.com/SteamDeckHomebrew/decky-plugin-template)
- [SimpleDeckyTDP Reference Plugin](https://github.com/aarron-lee/SimpleDeckyTDP)
