# Jetson Orin Nano setup: USB installer, NVMe, SSH

**Current status, November 8, 2026: firmware updated; NVMe reinstall in progress.** The post-update UEFI screen directly verifies **`39.2.1-gcid-46758480`**. Two update-progress screens were observed with black-screen/reboot intervals; no 100% frame was captured, so the verified result is the new UEFI version. At approximately **02:00 Pacific**, the agent explicitly selected **Boot Manager → SanDisk USB → Install r39.2.1 → Install on NVMe** under the user's fresh-setup authorization. The installer then began partitioning **`nvme0n1`**, directly visible at **02:01:41 Pacific / 10:01:41 UTC**. **No separate erase-confirmation prompt appeared before partitioning.** Installation completion, installed-system boot and persistent SSH remain unverified. Keep power and installer media connected while it runs.

At approximately **01:17 Pacific / 09:17 UTC**, the user-started JetPack 7.2.1 / Jetson Linux 39.2.1 installer stopped with **“An error occurred. Press enter to start a shell.”** An agent-sent **Enter through the KVM** opened the diagnostic shell, directly verifying local keyboard delivery. The pre-recovery read-only `bios_version` check returned **`36.4.2-gcid-38685322`**, unchanged from the starting firmware. Prior NVMe partitioning, extraction and package progress remain recorded below; recovery SSH was access to this failed installer, not the final OS.

No microSD is available. The earlier JetPack 6 microSD plan is retained below only as an archival fallback. It is not the current setup sequence.

## Evidence and preparation status

- User-provided photos show UEFI firmware **`36.4.2-gcid-38685322`**, dated **2024-12-12**. This satisfies NVIDIA's firmware prerequisite for the USB route; it does not establish an installed OS. [NVIDIA firmware check](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/quick_start.html#firmware-gate).
- The photographed **`Start HTTP Boot over IPv4`** screen is a network-boot attempt, not proof of Ubuntu or SSH. A direct **DisplayPort monitor is now available** for the local boot menu and installer.
- The **JetPack 7.2.1 USB is ready**: the 5,035,601,920-byte ISO was downloaded from NVIDIA, its local SHA-256 recorded, and balenaEtcher 2.1.9 completed flashing and validation with one successful target. The SanDisk was then safely ejected, and the user has since confirmed inserting it into the Jetson. See [USB preparation evidence](../validation/jetson-usb-prep-2026-11-08.json). The user selected the install entry; installation progressed on the webcam before the late-stage failure at 01:17 Pacific. The older 6.2.1 SD ZIP is not this USB installer.
- Earlier captures showed `nvme0n1` partitions 1–15 configured, partitioning/extraction finished, and NVIDIA/Ubuntu package installation underway. The failure in late/run/command_27 is now narrowed to the model/compatible mismatch detailed below. Read-only `lsblk` verified **`/dev/nvme0n1`**, **931.5G**, model **`SHPP41-1000GM`**, with partitions 1–15 and the target root mounted at **`/target`**. Prior contents and a successful installed-system boot remain unverified. That diagnostic-shell result predates the now-verified UEFI version `39.2.1-gcid-46758480`.
- JetPack 7.2.1 uses **Ubuntu 24.04**, while the archived JetPack 6 route uses Ubuntu 22.04. Do not apply the old `r36.5` repository edits or 6.x APT-upgrade commands on the new 7.x system. [NVIDIA release information](https://developer.nvidia.com/embedded/jetpack/downloads).

## Prepare the authorized USB

The completed Jetson ISO is linked from [NVIDIA's current downloads](https://developer.nvidia.com/embedded/jetpack/downloads). Preparation files are staged under `/Volumes/Fadi/Jetson-Orin-Nano-Setup-2026-11-07`; `USB-PREPARATION.json` and `jetpack-7.2.1-usb-write-verification.json` record the current route. `START-HERE.txt` now describes USB installation; the earlier SD entrypoint is retained as `START-HERE-2026-11-07-SD-plan.txt`. The [November 7 evidence](../validation/jetson-prep-2026-11-07.json) is historical SD/runtime preparation.

Re-identify the **SanDisk Cruzer Blade, 16 GB**, previously mounted as `UBUNTU 18_0`, immediately before flashing; macOS disk numbers change. Write and validate using installed balenaEtcher, or a guarded raw-device writer that checks the exact device and verifies the entire written image by readback. The original media-write authorization covered this USB stick; the later fresh-setup authorization covers the current NVMe reinstall described above. Retain the completed ISO filename, size, SHA-256 and write-validation outcome. A locally calculated hash alone is not comparison with a vendor-published checksum.

## Connections

Use the direct DisplayPort monitor for this installation. Keep the included 19 V power supply connected and provide a verified keyboard path. Keep robot CAN adapters disconnected during OS setup; check the heatsink and fan before workloads.

Latest wiring: Mac Ethernet → GL-RM1; Mac USB-C → KVM power; KVM data/HID **USB-C → USB-C** to the Jetson. The earlier USB-C → Jetson USB-A connection was not working. The KVM's HDMI input and USB-A host port are empty; the latter is not needed for keyboard control. Separately, **Jetson Ethernet → Beryl AX LAN** provided temporary recovery SSH at **`192.168.8.136`**, independently verified with a dedicated key before reboot. The earlier loopback-only query predates that working connection. The KVM remains on the Mac's shared Ethernet network; the complete kit is not yet independent of Mac Internet Sharing.

**KVM setup verified on November 8:** Mac Internet Sharing **Wi-Fi → Ethernet** is active on `bridge100` at `192.168.2.1`; the KVM received DHCP address **`192.168.2.2`**. Fresh local administrator setup completed after the requested reset. The local UI reports firmware **V1.9.2 release1 (RM1)**. Its generated administrator password is stored in the Mac login Keychain; no credential value is recorded here.

The user-approved Google sign-in and GLKVM **Bind** completed. Device **`GL-RM1-569`** is green/online in the cloud at `192.168.2.2`, with no paid plan activated. This verifies account binding and online status; an actual remote-control session remains unverified. Keep passwords, tokens, account identifiers and verification codes out of Git.

**USB host attachment is now directly verified, and keyboard interaction worked according to the user.** After the USB-C-to-USB-C data-cable reconnect, the user reported working keyboard control and selected the install entry described as “Install …39.something.” A fresh read-only KVM terminal check returned **`configured` / `high-speed`** for USB gadget state/current speed, clearing the earlier connection-level blocker. After installation failed, an agent-sent **Enter** opened the prompted root diagnostic shell, directly observed through the webcam. This verifies the local KVM HID path; off-site/cloud remote control remains a separate unverified capability.

Earlier evidence is retained: two virtual **V** attempts produced no character at `Shell>`, the tooltip said **“Keyboard captured, inactive,”** and a read-only KVM terminal check of `/sys/class/udc/*/state` and current speed returned **`not attached` / `UNKNOWN`**. Those results describe the earlier cable state, not the current user-reported working connection.

The user-authorized **Logitech C270** on the Mac can observe the DisplayPort monitor. Earlier 1280×720 captures showed an idle **UEFI Interactive Shell `Shell>` prompt**; later captures showed Subiquity/curtin progress, then the explicit failure and root diagnostic shell. The view is angled; keep the full screen visible to distinguish progress, errors and completion. Its AVFoundation index changed from 1 to 0 during setup, so re-identify the C270 before future captures.

The **GL.iNet Comet GL-RM1** remains an optional console: **Jetson DisplayPort → DP-to-HDMI adapter/cable → Comet HD IN**; separately connect **Jetson USB host → Comet USB-Device**, plus Comet power/network. The available DisplayPort/USB-C cable is not a video connection to this KVM. [GL.iNet connection guide](https://docs.gl-inet.com/kvm/en/user_guide/gl-rm1/quick_setup_guide/). Jetson USB-C does not output video; NVIDIA supports passive or active DP-to-HDMI adapters. [NVIDIA hardware layout](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/hardware_layout.html).

## Failed installer evidence and firmware recovery

The saved log identifies **late/run/command_27**, which inspects board model/compatible data and tries to select a matching `-nv` DTB. This corrects the earlier screen-based command23 transcription. It reported **“No matching -nv DTB found for running board”** and the verified crash file **`/var/crash/1751465839.266846180.install_fail.crash`**. Read-only comparisons now establish the immediate mismatch:

- Running model: **`NVIDIA Jetson Orin Nano Developer Kit`**.
- Running compatible values: **`nvidia,p3768-0000+p3767-0005-super`**, **`nvidia,p3767-0005`**, **`nvidia,tegra234`**.
- The Super DTB model is **`NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super`**, which differs from the running model.
- The standard DTB has the same model as the running board, but its compatible value lacks **`-super`**. Neither candidate satisfies both fields for the running board.

This explains why the matching step rejected the candidates; it does not establish that the board is unsupported or justify substituting a DTB. No DTB override has been applied. `cat /sys/class/dmi/id/bios_version` returned **`36.4.2-gcid-38685322`**; `nvbootctrl` was unavailable in the installer, so no slot-status result was obtained.

Temporary SSH to **`root@192.168.8.136`** with a dedicated key enabled evidence collection. `/var/log/installer` and crash evidence are preserved in the private archive **`/Volumes/Fadi/Jetson-Orin-Nano-Setup-2026-11-07/private-recovery-20261108/failed-installer-evidence.tar.gz`**, SHA-256 **`3a0f81d9499911ef5871481e5c1d379bbe9721ba2ec692c0df016d4696c4cc98`**. The local hash was independently rechecked, and the verified crash file is included. Installer log timestamps show **2025-07-02 because its clock was stale**; they are not the wall time of this November 8 recovery. Raw logs, keys and credentials remain outside Git; this record contains only the selected diagnostic facts.

After preservation, the Jetson was rebooted into firmware setup. [NVIDIA's startup reference](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/quick_start.html) directs explicit USB selection in Boot Manager, **Y within the 30-second QSPI prompt**, and waiting for **both capsule-update passes**, including expected reboots. UEFI Boot Manager explicitly selected the SanDisk USB. At the timed prompt before GRUB, **Y was sent with 19 seconds remaining**; `/private/tmp/jetson-firmware-recovery-06.png` confirms **“User confirmed. Proceeding with capsule update.”** Two subsequent update-progress screens were seen with black-screen/reboot intervals. Although no 100% frame was captured, the later UEFI screen in `/private/tmp/jetson-firmware-watch-hand-observation-11.png` directly verifies **`39.2.1-gcid-46758480`**, establishing the firmware result. Do not treat the previous temporary SSH session as current reachability after reboot.

The agent then selected the SanDisk USB explicitly, followed by **Install r39.2.1 → Install on NVMe**, at approximately 02:00 Pacific. The target had been identified as **SHPP41-1000GM, 931.5G** and fresh setup was user-authorized. Partitioning started automatically without a separate erase-confirmation prompt; `/private/tmp/jetson-retry-watch-hand-observation-03.png` shows `nvme0n1` partition work at 02:01:41 Pacific. **Do not interrupt the active reinstall or assume another confirmation will appear.** For future attempts, treat the Install/autoinstall selection itself as potentially destructive. Suggested hostname remains `orin-arm`; successful installation, final account, root device and persistent SSH still need verification.

## Proposed portable LAN — separate travel router

The user wants a portable Jetson/KVM kit whose local network does not depend on Mac Internet Sharing. The separate router is **GL.iNet GL-MT3000 Beryl AX**. After recovering the administrator password, the user started the authorized upgrade; download progress was seen at 38%, and the later admin UI directly confirms **v4.11.0**. An HTTP success check confirms internet remains available. The local screenshot is `/private/tmp/jetson-beryl-upgrade-check.png`; do not publish it or copy its Wi-Fi identifiers into Git.

**Preserve the home GL-MT6000.** It previously occupied `192.168.8.1` on the Mac's home Wi-Fi. The Mac joined Beryl Wi-Fi and received **`192.168.8.174`**; the gateway at **`192.168.8.1` is now the GL-MT3000**, verified by its network identity and admin-page model label. Identify the router by model and network, not IP alone. Beryl admin access and its upgrade are verified; the home router remains unchanged.

The working recovery wiring was **Jetson Ethernet → Beryl AX LAN**, with **KVM → Mac Ethernet** preserved. Temporary installer SSH verified the Jetson LAN path. On the upgraded Beryl, **Port 1 (physically WAN-labeled) was set to LAN**; the UI showed **Applied**, and the reloaded table retained LAN mode. This verifies the saved setting, not access through that port. The user has been asked to move the KVM Ethernet cable from the Mac to this free port; the reply and physical move are pending. Both devices still need testing without Mac sharing. Off-site access through the selected cloud/VPN route needs a separate end-to-end test. Neither the router upgrade nor temporary recovery SSH establishes the final installed host or off-site access.

## Verify the installed system and SSH

After the failure is resolved and a successful installed-system boot is verified:

1. Record `uname -a`, `cat /etc/nv_tegra_release`, `cat /etc/os-release`, `sudo nvbootctrl dump-slots-info`, `python3 --version`, `findmnt /`, `lsblk -f` and `df -h /`. Confirm the root filesystem belongs to the intended NVMe before changing packages. Compare actual L4T/Ubuntu versions with the selected 39.2.1/24.04 route.
2. Install `openssh-server`, enable it with `sudo systemctl enable --now ssh`, and record `hostname -I`. Connect from the Mac with the actual username/IP; verify the host key against the local console, install the intended public SSH key, and verify another key-based login.
3. Keep the default power setting initially; record `sudo nvpmodel -q`. Check cooling, fan and temperatures before performance tuning. LAN SSH is sufficient to start; join the existing Tailscale network when remote station access is needed.
4. Continue with [the robot-server handoff](jetson-end-to-end.md) for checkout, environment and offline tests. No CAN or motor action is part of OS preparation.

## Cached runtime and project

The staging folder contains `jetson-runtime-ready.tar.gz`: Linux ARM64 uv 0.13.7, Python 3.12.15, pinned Maker Arm SDK source, 15 base dependency wheels and two build-tool wheels. The refreshed `geotech-robotics-hackathon-119d38b.bundle` contains complete Git history at that main commit and passes `git bundle verify`; the earlier snapshot is retained. All 15 pinned base packages and the SDK commit still match the cache at `119d38b`. These artifacts are separate from the new ISO download. The runtime includes its source/hash manifest and offline instructions; its ARM64 code has not been validated on this Jetson.

Transfer after the installed OS and its persistent SSH access are verified. The temporary failed-installer SSH session is not the deployment target. Follow the archive instructions, clone the bundle or current repository, restore the GitHub remote, synchronize `main` without discarding work, and enter the checkout before using relative `arm/...` commands. `ARMCTL_PYTHON` can select the cached environment. Compare current requirements against this frozen cache first.

Keep the arm environment on **Python 3.12**: the pinned NumPy/SciPy require it, and the pickup client imports SciPy at startup. Verify the interpreter rather than assuming Ubuntu's version; keep system Python intact. No local training, PyTorch or CUDA application stack is required for the controller. OS libraries and firmware are not supplied by the Python bundle.

The separate `vision/run.py` preview uses OpenCV camera capture, but `vision/README.md` currently documents a JetPack 6 Torch setup. Verify a JetPack 7.2.1-compatible vision environment before using it here. The base arm cache does not contain that vision stack, and the preview does not establish the three-camera snapshot contract required by the pickup workflow.

## Board facts — update from observed results

| Field | Value |
| --- | --- |
| Model / RAM | Running device-tree model: NVIDIA Jetson Orin Nano Developer Kit; compatible includes p3768-0000+p3767-0005-super; RAM not yet recorded |
| Firmware before | Photo verified: `36.4.2-gcid-38685322`, build date `2024-12-12` |
| Firmware in failed installer | Historical pre-recovery `bios_version`: `36.4.2-gcid-38685322` |
| Firmware after recovery | UEFI directly verified: `39.2.1-gcid-46758480`; no 100% update frame captured |
| Current boot observation | Agent selected Install on NVMe after firmware verification; new nvme0n1 partitioning visible at 02:01:41 Pacific |
| Target JetPack / L4T / Ubuntu | Reinstall route: 7.2.1 / 39.2.1 / 24.04; installation in progress, successful final OS boot unverified |
| Console | DisplayPort/C270 view; KVM USB configured/high-speed; agent Enter directly opened diagnostic shell, HDMI empty |
| KVM local / cloud | Admin initialized; V1.9.2 release1 (RM1); GL-RM1-569 bound and online at 192.168.2.2; actual remote-control session unverified |
| KVM network | Mac Wi-Fi → Ethernet Internet Sharing active; bridge100 192.168.2.1, KVM DHCP 192.168.2.2 |
| Travel router | GL-MT3000 Beryl AX admin UI v4.11.0 after user-started upgrade; internet HTTP check passed; home GL-MT6000 unchanged |
| Portable LAN status | Beryl Port 1 saved as LAN; KVM cable move requested, physical-port access and full independence from Mac sharing pending |
| Jetson IPv4 | Temporary installer at 192.168.8.136 before reboot; installed-OS address not yet verified |
| Installer media | Validated 16 GB SanDisk Cruzer Blade; reused for verified firmware update and active NVMe reinstall |
| OS target | Pre-reinstall lsblk: /dev/nvme0n1, 931.5G, SHPP41-1000GM, partitions 1–15 and /target mount; current reinstall repartitioning, successful installed boot unverified |
| System Python / arm environment Python | Device results pending; isolated arm Python 3.12 planned |
| Hostname / username / Tailscale | Final account/hostname and Tailscale unverified; recovery used installer root account only |
| Power mode / fan / temperatures | Not yet verified |
| SSH key login / offline arm tests | Dedicated-key recovery SSH verified before reboot; persistent installed-OS SSH and arm tests unverified |

Never put passwords, private keys or API tokens in this table or Git.

## Archived fallback — JetPack 6 microSD and older-firmware bridge

**Not the current route.** These notes preserve the November 7 plan and downloaded assets. No microSD is currently available, and the photo-verified 36.4.2 firmware already passes the current USB prerequisite. Do not run the bridge just because its image was downloaded, and do not downgrade newly updated firmware to reuse old media.

If an explicit later decision selects JetPack 6, consult [NVIDIA's 6.x path](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/update_firmware.html) and confirm compatibility with the firmware then installed. That route uses a 64 GB-or-larger UHS-I microSD. Its older-firmware bridge uses updated **`JP513-orin-nano-sd-card-image_b29.zip`** and NVIDIA's scheduled firmware/QSPI-updater sequence before the target SD image. This bridge is for firmware older than 36.x, not the photographed board's current starting state.

The archived OS route is **JetPack 6.2.1 / L4T 36.4.4 / Ubuntu 22.04**, with [NVIDIA's documented upgrade to 6.2.3 / 36.5.2](https://forums.developer.nvidia.com/t/jetpack-6-2-3-jetson-linux-36-5-2-is-now-live/379872). Its `r36.5` APT changes apply only to that separately selected 6.x installation. Verify microSD root before package updates so an attached SSD is not changed accidentally.

Earlier SD downloads and integrity checks are recorded in [jetson-prep-2026-11-07.json](../validation/jetson-prep-2026-11-07.json). The 36.4.4 BSP hash manifest does not list the SD ZIP; local hashes establish recorded file identity, not a vendor checksum match. Preserve these artifacts as fallback materials, not as the active USB installer.
