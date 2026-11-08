# Jetson Orin Nano setup: USB installer, NVMe, SSH

**Current activity, November 8, 2026:** the **user started the JetPack 7.2.1 / Jetson Linux 39.2.1 USB installer** after reconnecting the KVM data cable and reporting that the keyboard worked. Webcam captures now directly show Ubuntu Subiquity/curtin installation on **`nvme0n1`**, progressing through partitioning and extraction to installed-system configuration. **Do not interrupt the active installation.** The reported attached 1 TB SSD's exact model, capacity and prior contents were not independently confirmed; installer completion, final firmware, installed OS and SSH remain unverified.

No microSD is available. The earlier JetPack 6 microSD plan is retained below only as an archival fallback. It is not the current setup sequence.

## Evidence and preparation status

- User-provided photos show UEFI firmware **`36.4.2-gcid-38685322`**, dated **2024-12-12**. This satisfies NVIDIA's firmware prerequisite for the USB route; it does not establish an installed OS. [NVIDIA firmware check](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/quick_start.html#firmware-gate).
- The photographed **`Start HTTP Boot over IPv4`** screen is a network-boot attempt, not proof of Ubuntu or SSH. A direct **DisplayPort monitor is now available** for the local boot menu and installer.
- The **JetPack 7.2.1 USB is ready**: the 5,035,601,920-byte ISO was downloaded from NVIDIA, its local SHA-256 recorded, and balenaEtcher 2.1.9 completed flashing and validation with one successful target. The SanDisk was then safely ejected, and the user has since confirmed inserting it into the Jetson. See [USB preparation evidence](../validation/jetson-usb-prep-2026-11-08.json). The user then selected the install entry, and active installation is directly visible on the webcam. The older 6.2.1 SD ZIP is not this USB installer.
- Fresh captures show `nvme0n1` partitions 1–15 configured, the partitioning and extract stages finished, then `curthooks` configuring the installed system and installing missing packages. The initial frame cut off the screen bottom; later adjusted captures establish this progress. The NVMe model/capacity, prior contents, final root device and completed installation remain unverified.
- JetPack 7.2.1 uses **Ubuntu 24.04**, while the archived JetPack 6 route uses Ubuntu 22.04. Do not apply the old `r36.5` repository edits or 6.x APT-upgrade commands on the new 7.x system. [NVIDIA release information](https://developer.nvidia.com/embedded/jetpack/downloads).

## Prepare the authorized USB

The completed Jetson ISO is linked from [NVIDIA's current downloads](https://developer.nvidia.com/embedded/jetpack/downloads). Preparation files are staged under `/Volumes/Fadi/Jetson-Orin-Nano-Setup-2026-11-07`; `USB-PREPARATION.json` and `jetpack-7.2.1-usb-write-verification.json` record the current route. `START-HERE.txt` now describes USB installation; the earlier SD entrypoint is retained as `START-HERE-2026-11-07-SD-plan.txt`. The [November 7 evidence](../validation/jetson-prep-2026-11-07.json) is historical SD/runtime preparation.

Re-identify the **SanDisk Cruzer Blade, 16 GB**, previously mounted as `UBUNTU 18_0`, immediately before flashing; macOS disk numbers change. Write and validate using installed balenaEtcher, or a guarded raw-device writer that checks the exact device and verifies the entire written image by readback. The authorization covers erasing this USB stick only. Retain the completed ISO filename, size, SHA-256 and write-validation outcome. A locally calculated hash alone is not comparison with a vendor-published checksum.

## Connections

Use the direct DisplayPort monitor for this installation. Keep the included 19 V power supply connected and provide a verified keyboard path. Keep robot CAN adapters disconnected during OS setup; check the heatsink and fan before workloads.

Latest user-reported wiring: Mac Ethernet → GL-RM1; Mac USB-C → KVM power; the KVM data/HID cable was reconnected **USB-C → USB-C** to the Jetson, after which keyboard interaction worked. The earlier USB-C → Jetson USB-A connection was not working. The KVM's HDMI input and USB-A host port are empty; the latter is not needed for keyboard control. This Ethernet cable serves the KVM, not the Jetson; arrange the Jetson's networking during first boot.

**KVM setup verified on November 8:** Mac Internet Sharing **Wi-Fi → Ethernet** is active on `bridge100` at `192.168.2.1`; the KVM received DHCP address **`192.168.2.2`**. Fresh local administrator setup completed after the requested reset. The local UI reports firmware **V1.9.2 release1 (RM1)**. Its generated administrator password is stored in the Mac login Keychain; no credential value is recorded here.

The user-approved Google sign-in and GLKVM **Bind** completed. Device **`GL-RM1-569`** is green/online in the cloud at `192.168.2.2`, with no paid plan activated. This verifies account binding and online status; an actual remote-control session remains unverified. Keep passwords, tokens, account identifiers and verification codes out of Git.

**USB host attachment is now directly verified, and keyboard interaction worked according to the user.** After the USB-C-to-USB-C data-cable reconnect, the user reported working keyboard control and selected the install entry described as “Install …39.something.” A fresh read-only KVM terminal check returned **`configured` / `high-speed`** for USB gadget state/current speed, clearing the earlier connection-level blocker. The agent has not independently sent and observed a successful keypress; no further test key is needed during this active installation.

Earlier evidence is retained: two virtual **V** attempts produced no character at `Shell>`, the tooltip said **“Keyboard captured, inactive,”** and a read-only KVM terminal check of `/sys/class/udc/*/state` and current speed returned **`not attached` / `UNKNOWN`**. Those results describe the earlier cable state, not the current user-reported working connection.

The user-authorized **Logitech C270** on the Mac can observe the DisplayPort monitor. Earlier 1280×720 captures showed an idle **UEFI Interactive Shell `Shell>` prompt**; the latest adjusted captures show active Subiquity/curtin installation. The view is angled; keep the full screen visible to distinguish progress, errors and completion. Its AVFoundation index changed from 1 to 0 during setup, so re-identify the C270 before future captures.

The **GL.iNet Comet GL-RM1** remains an optional console: **Jetson DisplayPort → DP-to-HDMI adapter/cable → Comet HD IN**; separately connect **Jetson USB host → Comet USB-Device**, plus Comet power/network. The available DisplayPort/USB-C cable is not a video connection to this KVM. [GL.iNet connection guide](https://docs.gl-inet.com/kvm/en/user_guide/gl-rm1/quick_setup_guide/). Jetson USB-C does not output video; NVIDIA supports passive or active DP-to-HDMI adapters. [NVIDIA hardware layout](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/hardware_layout.html).

## Active installation — observe without interrupting

The user has already launched installation; partitioning and source extraction are visibly complete, and installed-system configuration is running. Keep power and the installer USB connected. Do not restart, return to Boot Manager, send exploratory keys or remove media while this work continues. Wait for the installer's completion/reboot instructions and capture the complete screen.

Do not promise a later disk-confirmation prompt: this attempt has already written partitions on `nvme0n1`. For any future reinstall, treat an **Install/autoinstall menu selection as potentially destructive** and confirm the intended device and erasure before selecting it. The user launched the current installation.

[NVIDIA's startup reference](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/quick_start.html) covers explicit USB selection in Boot Manager, the 30-second **Y** QSPI prompt and two capsule-update passes. Completion of those firmware passes was not independently observed in this run. Verify the final firmware version after installation instead of restarting to repeat them.

Suggested hostname after installation: `orin-arm`; record the actual username. Installation progress is not proof of a successful installed-system boot.

## Verify the installed system and SSH

After the active installation completes and the Jetson boots from its installed target:

1. Record `uname -a`, `cat /etc/nv_tegra_release`, `cat /etc/os-release`, `sudo nvbootctrl dump-slots-info`, `python3 --version`, `findmnt /`, `lsblk -f` and `df -h /`. Confirm the root filesystem belongs to the intended NVMe before changing packages. Compare actual L4T/Ubuntu versions with the selected 39.2.1/24.04 route.
2. Install `openssh-server`, enable it with `sudo systemctl enable --now ssh`, and record `hostname -I`. Connect from the Mac with the actual username/IP; verify the host key against the local console, install the intended public SSH key, and verify another key-based login.
3. Keep the default power setting initially; record `sudo nvpmodel -q`. Check cooling, fan and temperatures before performance tuning. LAN SSH is sufficient to start; join the existing Tailscale network when remote station access is needed.
4. Continue with [the robot-server handoff](jetson-end-to-end.md) for checkout, environment and offline tests. No CAN or motor action is part of OS preparation.

## Cached runtime and project

The staging folder contains `jetson-runtime-ready.tar.gz`: Linux ARM64 uv 0.13.7, Python 3.12.15, pinned Maker Arm SDK source, 15 base dependency wheels and two build-tool wheels. The refreshed `geotech-robotics-hackathon-119d38b.bundle` contains complete Git history at that main commit and passes `git bundle verify`; the earlier snapshot is retained. All 15 pinned base packages and the SDK commit still match the cache at `119d38b`. These artifacts are separate from the new ISO download. The runtime includes its source/hash manifest and offline instructions; its ARM64 code has not been validated on this Jetson.

Transfer after SSH works. Follow the archive instructions, clone the bundle or current repository, restore the GitHub remote, synchronize `main` without discarding work, and enter the checkout before using relative `arm/...` commands. `ARMCTL_PYTHON` can select the cached environment. Compare current requirements against this frozen cache first.

Keep the arm environment on **Python 3.12**: the pinned NumPy/SciPy require it, and the pickup client imports SciPy at startup. Verify the interpreter rather than assuming Ubuntu's version; keep system Python intact. No local training, PyTorch or CUDA application stack is required for the controller. OS libraries and firmware are not supplied by the Python bundle.

The separate `vision/run.py` preview uses OpenCV camera capture, but `vision/README.md` currently documents a JetPack 6 Torch setup. Verify a JetPack 7.2.1-compatible vision environment before using it here. The base arm cache does not contain that vision stack, and the preview does not establish the three-camera snapshot contract required by the pickup workflow.

## Board facts — update from observed results

| Field | Value |
| --- | --- |
| Model / RAM | Orin Nano reported; exact module/RAM still unverified |
| Firmware before | Photo verified: `36.4.2-gcid-38685322`, build date `2024-12-12` |
| Firmware after installer | Not yet verified |
| Current boot observation | User started installer; webcam shows `nvme0n1` partitioning/extraction finished and installed-system configuration in progress |
| Target JetPack / L4T / Ubuntu | Installer route: 7.2.1 / 39.2.1 / 24.04; installation active, final versions unverified |
| Console | DisplayPort monitor visible through C270; KVM USB state directly verified configured/high-speed; keyboard worked per user, agent keypress verification pending, HDMI empty |
| KVM local / cloud | Admin initialized; V1.9.2 release1 (RM1); GL-RM1-569 bound and online at 192.168.2.2; actual remote-control session unverified |
| KVM network | Mac Wi-Fi → Ethernet Internet Sharing active; bridge100 192.168.2.1, KVM DHCP 192.168.2.2 |
| Installer media | 16 GB SanDisk Cruzer Blade flashed/validated by Etcher 2.1.9; inserted into Jetson, installer now running |
| OS target | Installer has configured `nvme0n1` partitions 1–15; exact model/capacity and prior contents not independently verified |
| System Python / arm environment Python | Device results pending; isolated arm Python 3.12 planned |
| Hostname / username / LAN or Tailscale address | Not yet verified |
| Power mode / fan / temperatures | Not yet verified |
| SSH key login / offline arm tests | Not yet verified |

Never put passwords, private keys or API tokens in this table or Git.

## Archived fallback — JetPack 6 microSD and older-firmware bridge

**Not the current route.** These notes preserve the November 7 plan and downloaded assets. No microSD is currently available, and the photo-verified 36.4.2 firmware already passes the current USB prerequisite. Do not run the bridge just because its image was downloaded, and do not downgrade newly updated firmware to reuse old media.

If an explicit later decision selects JetPack 6, consult [NVIDIA's 6.x path](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/update_firmware.html) and confirm compatibility with the firmware then installed. That route uses a 64 GB-or-larger UHS-I microSD. Its older-firmware bridge uses updated **`JP513-orin-nano-sd-card-image_b29.zip`** and NVIDIA's scheduled firmware/QSPI-updater sequence before the target SD image. This bridge is for firmware older than 36.x, not the photographed board's current starting state.

The archived OS route is **JetPack 6.2.1 / L4T 36.4.4 / Ubuntu 22.04**, with [NVIDIA's documented upgrade to 6.2.3 / 36.5.2](https://forums.developer.nvidia.com/t/jetpack-6-2-3-jetson-linux-36-5-2-is-now-live/379872). Its `r36.5` APT changes apply only to that separately selected 6.x installation. Verify microSD root before package updates so an attached SSD is not changed accidentally.

Earlier SD downloads and integrity checks are recorded in [jetson-prep-2026-11-07.json](../validation/jetson-prep-2026-11-07.json). The 36.4.4 BSP hash manifest does not list the SD ZIP; local hashes establish recorded file identity, not a vendor checksum match. Preserve these artifacts as fallback materials, not as the active USB installer.
