# Jetson Orin Nano setup: USB installer, NVMe, SSH

**Current preparation route, November 8, 2026:** JetPack **7.2.1 / Jetson Linux 39.2.1** USB installer, with the attached **1 TB NVMe SSD** as the proposed OS target. The user has authorized erasing the old **16 GB SanDisk Cruzer Blade** for installation media. **SSD erasure/installation is not yet authorized:** confirm the intended SSD and its contents with the user at the installer's target-selection step before confirming installation.

No microSD is available. The earlier JetPack 6 microSD plan is retained below only as an archival fallback. It is not the current setup sequence.

## Evidence and preparation status

- User-provided photos show UEFI firmware **`36.4.2-gcid-38685322`**, dated **2024-12-12**. This satisfies NVIDIA's firmware prerequisite for the USB route; it does not establish an installed OS. [NVIDIA firmware check](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/quick_start.html#firmware-gate).
- The photographed **`Start HTTP Boot over IPv4`** screen is a network-boot attempt, not proof of Ubuntu or SSH. A direct **DisplayPort monitor is now available** for the local boot menu and installer.
- The new USB ISO download/preparation is **in progress**. Download completion, image verification, USB writing and USB validation are not yet recorded here. The older 6.2.1 SD ZIP is not this installer and must not be written to this 16 GB stick.
- The SSD's contents, partitions, model/serial and bootability remain unconfirmed. Its reported 1 TB capacity alone does not identify a safe erase target.
- JetPack 7.2.1 uses **Ubuntu 24.04**, while the archived JetPack 6 route uses Ubuntu 22.04. Do not apply the old `r36.5` repository edits or 6.x APT-upgrade commands on the new 7.x system. [NVIDIA release information](https://developer.nvidia.com/embedded/jetpack/downloads).

## Prepare the authorized USB

Use the Jetson ISO linked from [NVIDIA's current downloads](https://developer.nvidia.com/embedded/jetpack/downloads). Preparation files are staged under `/Volumes/Fadi/Jetson-Orin-Nano-Setup-2026-11-07`; inspect the actual new ISO and its verification record before writing. Earlier entries in `START-HERE.txt` and [the November 7 preparation evidence](../validation/jetson-prep-2026-11-07.json) describe the older SD downloads, not completion of this ISO.

Re-identify the **SanDisk Cruzer Blade, 16 GB**, previously mounted as `UBUNTU 18_0`, immediately before flashing; macOS disk numbers change. Write and validate using installed balenaEtcher, or a guarded raw-device writer that checks the exact device and verifies the entire written image by readback. The authorization covers erasing this USB stick only. Retain the completed ISO filename, size, SHA-256 and write-validation outcome. A locally calculated hash alone is not comparison with a vendor-published checksum.

## Connections

Use the direct DisplayPort monitor plus a USB keyboard for this installation. Connect Ethernet and the included 19 V power supply. Keep robot CAN adapters disconnected during OS setup; check the heatsink and fan before workloads.

The **GL.iNet Comet GL-RM1** remains an optional console: **Jetson DisplayPort → DP-to-HDMI adapter/cable → Comet HD IN**; separately connect **Jetson USB host → Comet USB-Device**, plus Comet power/network. The available DisplayPort/USB-C cable is not a video connection to this KVM. [GL.iNet connection guide](https://docs.gl-inet.com/kvm/en/user_guide/gl-rm1/quick_setup_guide/). Jetson USB-C does not output video; NVIDIA supports passive or active DP-to-HDMI adapters. [NVIDIA hardware layout](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/hardware_layout.html).

## Supervised installer sequence after USB validation

Follow [NVIDIA's Orin Nano quick start](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/quick_start.html) at the local console:

1. Connect the validated installer USB. At the NVIDIA splash press **Esc → Boot Manager**, then select the USB device explicitly, rather than the HTTP/network entry.
2. Watch for the QSPI firmware confirmation. With existing 36.x firmware, press **Y within 30 seconds** when proceeding with the firmware update. A missed prompt requires restarting the installer.
3. Allow **both capsule-update passes** and their expected reboots to finish. Never interrupt power. Record the resulting firmware version.
4. Choose **Install Jetson ISO r39.2.1**, then inspect storage selection. **Stop before confirming installation:** show the proposed NVMe's device identity, capacity and any displayed partitions to the user. Obtain confirmation to erase that intended 1 TB SSD; do not substitute another disk or the USB installer.
5. Only after that confirmation, continue installation. The selected target is erased. Follow the reboot/media-removal prompt, then finish licence, account and network setup.

Suggested hostname: `orin-arm`; record the actual username. Booting the installer or completing its firmware phase does not prove the SSD installation succeeded.

## Verify the installed system and SSH

After installation has been authorized and completed:

1. Record `uname -a`, `cat /etc/nv_tegra_release`, `cat /etc/os-release`, `sudo nvbootctrl dump-slots-info`, `python3 --version`, `findmnt /`, `lsblk -f` and `df -h /`. Confirm the root filesystem belongs to the intended NVMe before changing packages. Compare actual L4T/Ubuntu versions with the selected 39.2.1/24.04 route.
2. Install `openssh-server`, enable it with `sudo systemctl enable --now ssh`, and record `hostname -I`. Connect from the Mac with the actual username/IP; verify the host key against the local console, install the intended public SSH key, and verify another key-based login.
3. Keep the default power setting initially; record `sudo nvpmodel -q`. Check cooling, fan and temperatures before performance tuning. LAN SSH is sufficient to start; join the existing Tailscale network when remote station access is needed.
4. Continue with [the robot-server handoff](jetson-end-to-end.md) for checkout, environment and offline tests. No CAN or motor action is part of OS preparation.

## Cached runtime and project

The staging folder contains `jetson-runtime-ready.tar.gz`: Linux ARM64 uv 0.13.7, Python 3.12.15, pinned Maker Arm SDK source, 15 base dependency wheels and two build-tool wheels. It also contains `geotech-robotics-hackathon-4070db1.bundle`, a Git snapshot at that main commit. These previously prepared artifacts are separate from the new ISO download. The runtime includes its source/hash manifest and offline instructions; its ARM64 code has not been validated on this Jetson.

Transfer after SSH works. Follow the archive instructions, clone the bundle or current repository, restore the GitHub remote, synchronize `main` without discarding work, and enter the checkout before using relative `arm/...` commands. `ARMCTL_PYTHON` can select the cached environment. Compare current requirements against this frozen cache first.

Keep the arm environment on **Python 3.12**: the pinned NumPy/SciPy require it, and the pickup client imports SciPy at startup. Verify the interpreter rather than assuming Ubuntu's version; keep system Python intact. No local training, PyTorch or CUDA application stack is required for the controller. OS libraries and firmware are not supplied by the Python bundle.

## Board facts — update from observed results

| Field | Value |
| --- | --- |
| Model / RAM | Orin Nano reported; exact module/RAM still unverified |
| Firmware before | Photo verified: `36.4.2-gcid-38685322`, build date `2024-12-12` |
| Firmware after installer | Not yet verified |
| Current boot observation | Photo shows HTTP boot over IPv4; installed OS not established |
| Target JetPack / L4T / Ubuntu | Prepared route: 7.2.1 / 39.2.1 / 24.04; installation pending |
| Console | Direct DisplayPort monitor available; Comet route still needs correct HDMI path |
| Installer media | 16 GB SanDisk Cruzer Blade erase authorized; ISO download/write validation pending |
| OS target | Attached 1 TB NVMe proposed; identity/content confirmation and erase authorization pending |
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
