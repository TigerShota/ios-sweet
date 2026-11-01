# ios-sweet

**Apple's iOS 16 running on a Redmi Note 9 Pro 5G.**

This project ports Apple's open-source XNU kernel (xnu-8796, the iOS 16 generation) to the Xiaomi Redmi Note 9 Pro 5G (`gauguin`, Qualcomm SM7225 / Snapdragon 750G). On top of it runs the **unmodified iOS 16.7.16 (20H392) userland of the iPhone X**: launchd, the system daemons, backboardd, SpringBoard and the stock apps.

It is a port of [dipa-studio/ios-sweet](https://github.com/dipa-studio/ios-sweet) (the original Redmi Note 10 Pro / `sweet` / SM7150 bring-up). The XNU patch series and the boot shim are shared; the device-specific parts — device tree, memory map, PMIC buttons, touch controller — are gauguin's.

As of **2026-11-01** the phone boots **past the Apple logo into the SpringBoard UI**. The first boot needs a long soak (~10 minutes) for the one-time first-boot database reseed with a cold MobileGestalt cache. Rendering runs entirely on the CPU; there is no GPU driver, and touch does not work yet (the Novatek touch driver is the current milestone).

> Experimental bring-up, not a usable phone OS. Many things are missing or stubbed (see below), and Apple's security model is mostly switched off. It installs into the `userdata` partition and so **erases Android's user data**.

## What works

| Area | State |
| --- | --- |
| Boot | Stock Xiaomi bootloader → `fastboot boot` of our shim → XNU. Nothing is flashed to the boot partitions |
| CPU | 6 of the 8 cores via PSCI (`cpus=6`; the ADT carries all 8 nodes — 2× Cortex-A76 + 6× Cortex-A55), 16K pages, GICv3 interrupts, timers |
| Memory | 896 MiB window in the high DDR bank below 4 GiB (`0xc8000000`) — every larger or low-bank window hits the splash-framebuffer carve or the non-RAM hole (see NOTES.md) |
| Storage | Own UFS host driver; the iOS root is a journaled HFS+ volume in `userdata`, read/write |
| iOS userland | Real iPhone X 16.7.16 root filesystem and shared cache; launchd and the system daemons run |
| Display | Kernel IOSurface + IOMobileFramebuffer (`AppleCLCD`) services; CoreAnimation renders in software; frames are copied into the bootloader's framebuffer. Presented to iOS as an iPhone X panel (1125×2436) |
| GUI | backboardd and SpringBoard come up past the logo |
| Buttons | Power over the PMIC PON block (SPMI, verified on device); volume-down is the PON RESIN input; volume-up is PM6350 SID 0 GPIO2 (PPID `0x0c1`) — fixed in 0060, kernel rebuilt, boot verification pending |
| Device identity | iPhone X (`iPhone10,3` / `D22AP`): no Home button, gesture UI |
| Activation | Native "Hactivation" path of mobileactivationd. No Apple servers involved |
| Diagnostics | Persistent 4 MiB log ring at the end of `userdata`, per-process stack sampler, IOKit trace and console crash reports — there is no serial console, these carry the whole bring-up |

## What does not work (yet)

- **Touch.** gauguin's touch IC is a Novatek NT36672C on SPI (not I²C like sweet's Goodix), with host-loaded firmware from the vendor partition. Writing that driver is the current milestone.
- **No GPU.** Everything is drawn by the CPU (soft-composited framebuffer), like the original sweet port.
- **No Wi-Fi, Bluetooth, cellular, audio, camera, sensors, GPS or SEP / Face ID.** `bluetoothd` and `fairplayd.H2` crash-loop.
- **Slow first boot:** about 10 minutes on the Apple logo. Second-boot speed has not been verified yet.
- **Security is off:** permissive code signing (AMFI) and sandbox policies, a stub AppleKeyStore, no data protection. Do not put anything personal on it.

## How it boots

```
ABL (stock Xiaomi bootloader, unlocked)
  -> fastboot boot shim-boot.img              (temporary; nothing is flashed to boot)
     -> shim: own MMU setup, reads the live FDT, loads the payload from the ramdisk
        -> XNU Mach-O + generated Apple Device Tree (ADT) + boot-args
           -> XNU -> UFS driver -> HFS+ root in the userdata partition
              -> dyld + shared cache -> launchd -> daemons -> backboardd + SpringBoard
```

There is no serial console. The kernel draws its console into the framebuffer that ABL leaves behind, and keeps a persistent 4 MiB log ring at the end of `userdata`. You read that log from OrangeFox recovery with `tools/sweet-log-pull.sh`, so you don't have to film the screen.

## How to do it yourself

You need a Redmi Note 9 Pro 5G (`gauguin`) with an **unlocked bootloader** and a GitHub account. All builds run on GitHub Actions — this port was brought up from a Windows PC without any local toolchain. **Everything on `userdata` is lost.** Bootloaders, recovery, modem and persist partitions are never touched; the stock device tree the shim needs is carried in `gauguin/dtb`.

### 0. Back up the phone

Boot OrangeFox (or any recovery with adb root) temporarily and pull at least `persist`, `modemst1`, `modemst2`, `fsg`, `fsc` and `boot`, `dtbo`, `vbmeta`. They contain per-device calibration data. Keep them off git.

### 1. Build the kernel

The kernel builds on GitHub's macOS runners with [blacktop/darwin-xnu-build](https://github.com/blacktop/darwin-xnu-build). The workflow fetches xnu-8796, applies `xnu/patches/` in order, and also stages the pinned open-source libpthread, HFS and BearSSL sources it needs. Fork the repo (public repos get free macOS minutes) and run:

```sh
gh workflow run xnu.yml -R YOU/ios-sweet
gh run download RUN_ID -R YOU/ios-sweet -n xnu-kernel -D xnu/out
```

To check a patch locally before spending a CI run, `tools/xnu-cc.py` replays the compile commands of a previous CI log for single files.

### 2. Build the iOS root image

`gauguin-root.yml` does the whole thing on a Linux runner: it downloads the iPhone X (iPhone10,3) iOS 16.7.16 (20H392) IPSW from Apple, extracts the OS, SystemOS and AppOS disk images, and runs `tools/mkiosroot.sh` + `tools/sweet-root-tweaks.py` (Setup skip, activation seeding, daemon pruning). The artifact is the root image, gzipped and split:

```sh
gh workflow run gauguin-root.yml -R YOU/ios-sweet
gh run download RUN_ID -R YOU/ios-sweet -n ios16-root -D root
cat root/root.part-* > ios16-root.img.gz && gunzip ios16-root.img.gz
img2simg ios16-root.img ios16-root.simg
```

### 3. Build the boot image

`gauguin-shim.yml` fetches the kernel artifact (latest successful xnu run by default), generates the gauguin Apple Device Tree with `tools/mkadt.py` (memory window, 8 CPU nodes, iPhone X identity), packs kernel + ADT + boot-args into the payload, and builds the shim boot image against the stock DTB in `gauguin/dtb`:

```sh
gh workflow run gauguin-shim.yml -R YOU/ios-sweet
gh run download RUN_ID -R YOU/ios-sweet -n gauguin-shim-boot -D shim/out
```

What the less obvious boot-args (workflow defaults) do:

| Boot-arg | Effect |
| --- | --- |
| `rd=ufsroot0 ufsrw=1 rootrw=1` | Root on the `userdata` partition, writable |
| `cpus=6` | Start six cores; the ADT carries all 8 (`cpus=1` is the safe fallback) |
| `sweettouch=0`, `sweetkeys=0` | Disable the touch or button driver |
| `sweetfb_noconsole=1` | Stop drawing kernel text over the GUI. It still goes to the persistent log; panics are drawn |
| `sweetfb_hz=30` | Panel refresh rate of the CPU copy |
| `sweetsample=…`, `sweetiolog=…` | The per-process stack sampler and IOKit trace lists that drive the bring-up |

### 4. Flash the root and boot

```sh
fastboot -S 512M flash userdata ios16-root.simg   # ~7 minutes; do not unplug
# Xiaomi's fastboot often hangs after a large flash: re-enter fastboot with Power + Volume down
fastboot boot shim/out/shim-boot.img
```

The first boot takes about 10 minutes on the Apple logo, then the SpringBoard UI appears. To get back to fastboot, hold Power + Volume down.

### 5. Read the kernel log

```sh
fastboot boot recovery/recovery.img        # OrangeFox, temporary
tools/sweet-log-pull.sh logs/boot.txt      # 4 MiB console ring from the end of userdata
adb reboot bootloader
```

The log contains crash reports with backtraces (`CRASH name ...`), the sampler's per-thread stack segments, and the drivers' `SWEET ...` lines. `tools/sweet-crash.py` symbolicates them against the shared cache.

### QEMU

`tools/qemu-sweet.py` boots the same kernel on QEMU `virt` with a test RAM-disk root; the emulated RAM size follows the mkadt memory window. It is useful for kernel-only changes; see `docs/QEMU_TESTING.md`.

## Repository layout

| Path | What |
| --- | --- |
| `gauguin/` | gauguin's stock DTB and the generated base ADT |
| `shim/` | Bare-metal boot shim (C + asm) packed as an Android boot.img v2 |
| `xnu/patches/` | The XNU patch series (applied in order by CI), plus staging scripts for libpthread, HFS and BearSSL |
| `.github/workflows/xnu.yml` | Kernel CI build |
| `.github/workflows/gauguin-shim.yml` | Boot image build (kernel artifact + ADT + shim) |
| `.github/workflows/gauguin-root.yml` | iOS root image build from the IPSW |
| `.github/workflows/gauguin-dsc.yml`, `symbolize.yml` | dyld shared cache handling and crash symbolication |
| `tools/mkadt.py` | Generates the Apple Device Tree: gauguin memory window, 8 CPU nodes, iPhone X identity |
| `tools/mkpayload.py` | Packs kernel + ADT + boot-args into the shim payload |
| `tools/mkiosroot.sh` | Builds the HFS+ iOS root from the IPSW images |
| `tools/sweet-root-tweaks.py` | Bring-up changes to the root (Setup skip, activation, daemons) |
| `tools/sweet-log-pull.sh`, `sweet-log.py` | Read the persistent kernel log from recovery |
| `tools/sweet-crash.py` | Symbolicate console crash reports |
| `tools/xnu-cc.py`, `kernel-build-local.sh` | Local single-file compile check / local kernel build replicating CI |
| `tools/qemu-sweet.py` | QEMU test boots with framebuffer text decoding |
| `tools/adt.py`, `remotezip.py`, `unpack_bootv2.py`, `sparsediff.py` | Helpers |
| `NOTES.md` | Detailed engineering log: every boot, crash and decision, plus hardware facts |

Not in git: partition backups, live device trees, recovery images, Apple firmware and root images, and downloaded or generated build trees. They contain device serials and calibration data, or Apple's copyrighted files.

## Kernel patches

| # | What |
| --- | --- |
| 0001–0009 | Toolchain compatibility, SWEET machine config, non-PAC arm64, stubs for Apple-internal pieces |
| 0010–0018 | Early framebuffer console (0012 rewritten for gauguin: keep the debug console after the MMU turns on), physmap fix, PRNG without corecrypto, device-tree platform expert, timer PPI, boot CPU driver |
| 0019–0020 | Apple open-source libpthread kernel provider; BearSSL AES |
| 0021–0027 | RAM-disk root, mockfs, SHA-256, minimal AMFI, Apple open-source HFS+, MD5 |
| 0028–0034 | UFS host driver and root device, read/write root, DMA above 4 GiB |
| 0035–0045 | Entitlements, bindfs, AppleKeyStore stub, IOWatchdog, console crash reports |
| 0046 | IOSurface (IOCoreSurfaceRoot) and IOMobileFramebuffer (AppleCLCD) services |
| 0047–0055 | Diagnostics: persistent log, /dev/null tee, IOKit trace, heap strings and user stack sampler; permissive Sandbox policy; HFS rename-swap; AppleKeyStore selectors incl. kAKSWrapKey/UnwrapKey |
| 0056–0057 | Throttled panel copy; no external identity resolver |
| 0058 | HID event services. Apple's IOHIDFamily-1915.140.3 user client and event queue, matching the iOS 16.7 userland |
| 0059 | iPhone X identity (`hw.product` / `hw.target` from the device tree) |
| 0060 | Power and volume keys over SPMI; touch over GENI I²C (gauguin: volume-up reads PM6350 SID 0 GPIO2, PPID `0x0c1`) |
| 0061 | Present the display as 1125×2436 |
| 0062 | RPMh regulator vote for the touch rail; battery power source |
| 0063 | GENI FIFO packing and the Goodix GT9896 protocol (sweet's touch IC; gauguin's Novatek driver is still to be written) |
| 0064 | Grant sandbox privileges (PRIV_WORK_INTERVAL, PRIV_GLOBAL_PROC_INFO, …) |
| 0065 | SMP: secondary cores via PSCI CPU_ON |
| 0066–0067 | Fault-region crash info; console mute |
| 0068 | IOSurface lookup semantics for already-mapped surfaces |
| 0069 | Batched persistent-log flushes |
| 0070–0071 | pid_hibernate syscall; touch edge-tip events (upstream sweet navigation work) |
| 0072–0077, 0081 | USB device mode: DWC3 ep0, VBUS override, CDC-ECM `ue0`, RX mbufs, live log over UDP, link state (from upstream sweet; untested on gauguin) |
| 0074-gauguin, 0075-gauguin | gauguin bring-up aids: bsdinit trace; fbcon clear on full |
| 0078 | 8-core SMP (all CPU nodes) |
| 0079–0080 | Display power control; network route trace (upstream sweet) |
| 0082-gauguin | Extra early-io L2 slots — the 896 MiB window's early-io mapping needs two L2 slots |

## Key hardware facts

- RAM: 6 GB in two banks. Low bank `0x9b000000`–`0xbcbfffff` (splash framebuffer carved at `0xa0000000`, non-RAM `removed_region` hole `0xbcc00000`–`0xc7afffff`), high bank `0xc7b00000`–`0x1ffffffff`. Kernel window: `0xc8000000` + 896 MiB — the largest clean window: 384 MiB in the low bank starved iOS 16's first boot, 512/576 MiB spanned the hole, and 4 GiB+ addresses reset the board.
- GICv3 with security extensions: the timer goes through Group 1 IRQ (virtual timer INTID 19), not FIQ as on Apple silicon. PSCI through `smc`, 19.2 MHz timebase, ARMv8.2 without pointer authentication.
- Panel: 1080×2400; the shim reuses ABL's continuous splash framebuffer for scanout.
- Touch: Novatek NT36672C on SPI QUP SE0 (`0x880000`), CS on gpio3, reset tlmm 21, irq tlmm 22, max 10 MHz; firmware `novatek_nt36672c_j17_fw*.bin` host-loaded on every boot.
- Buttons: power (PON KPDPWR) and volume-down (PON RESIN) on the PM6350 PON block; volume-up on PM6350 SID 0 GPIO2 (PPID `0x0c1`), active low.

More in [`NOTES.md`](NOTES.md).

## Credits

Based on [dipa-studio/ios-sweet](https://github.com/dipa-studio/ios-sweet), the original Redmi Note 10 Pro port. Built on Apple's open-source releases (xnu, IOHIDFamily, libpthread, hfs), [BearSSL](https://bearssl.org), [blacktop/darwin-xnu-build](https://github.com/blacktop/darwin-xnu-build), [ipsw](https://github.com/blacktop/ipsw), Qualcomm's and Xiaomi's published Linux kernel sources (the button and touch facts come from the gauguin vendor device trees), and [OrangeFox Recovery](https://orangefox.download). iOS itself is Apple's and is not distributed here.
