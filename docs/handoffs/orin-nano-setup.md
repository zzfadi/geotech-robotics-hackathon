# Jetson Orin Nano setup: microSD, first boot, SSH

**Selected route, Nov 7, 2026:** use microSD for the fresh OS installation on FADI's reported Jetson Orin Nano. A 1 TB NVMe SSD is attached; its contents, partitions and bootability are unknown. Do not format or install onto it as part of this setup. Firmware lives on the board even when the OS card is empty.

This plan was checked against NVIDIA's resources below. No first boot, firmware update, Jetson dependency installation or hardware test has been verified on this board. Continue with [the robot-server handoff](jetson-end-to-end.md) after SSH works.

## Selected images and preparation

- **Boot image:** JetPack **6.2.1 / Jetson Linux 36.4.4**, then NVIDIA's supported APT update to **6.2.3 / 36.5.2**. Both use Ubuntu 22.04. This is NVIDIA's documented JetPack 6 microSD route.
- **Conditional bridge:** updated JetPack **5.1.3**, filename `JP513-orin-nano-sd-card-image_b29.zip`, only if the board has older QSPI firmware. Check the version before choosing the card image.
- Mac staging destination: `/Volumes/Fadi/Jetson-Orin-Nano-Setup-2026-11-07`. See `START-HERE.txt` there and the [preparation evidence](../validation/jetson-prep-2026-11-07.json) for completed downloads, sizes, hashes and verification scope. Etcher 2.1.9 is installed in `/Applications`.
- Check ZIP integrity and record each completed file's SHA-256. The linked NVIDIA 36.4.4 hash manifest covers BSP artifacts, **not the SD ZIP**; a locally computed hash is not a match against a published NVIDIA digest.
- Write the selected image with [balenaEtcher](https://etcher.balena.io), including its write validation. Identify the microSD by capacity/device before writing: flashing erases that card. Copying the ZIP onto a formatted card is insufficient.

## Cached runtime and project

The staging folder also contains `jetson-runtime-ready.tar.gz` (Linux ARM64 uv 0.13.7, Python 3.12.15, pinned Maker Arm SDK source, 15 base dependency wheels and two build-tool wheels) and `geotech-robotics-hackathon-4070db1.bundle` (complete Git history at that main commit). Transfer these after SSH works. The runtime archive includes its own source/hash manifest and offline instructions; its ARM64 code has not been executed on the Mac or Jetson.

`START-HERE.txt` explains cloning from the Git bundle, restoring the GitHub remote, synchronizing main, and selecting the cached environment with `ARMCTL_PYTHON`. Compare any newer requirement changes before installing from this frozen cache. NVIDIA firmware/APT upgrades and needed Ubuntu packages still require the board and network; they are not part of the Python bundle.

## What to plug in

- A **64 GB or larger UHS-I microSD**, plus a reader for the Mac. One card can be rewritten after the bridge; a second card saves that rewrite.
- The included **19 V power supply** and Ethernet for first networking.
- **KVM video from the Jetson's DisplayPort output.** For an HDMI KVM, use a **DP-to-HDMI** cable/adapter; NVIDIA supports passive and active adapters. Connect the KVM's keyboard/mouse USB link to a Jetson USB host port. The specific KVM still needs testing.
- The Jetson's **USB-C port has no video output**. It does not replace the DisplayPort cable or the DC power supply.
- Keep arm hardware disconnected during OS setup. Check that the heatsink and fan are fitted before running workloads.

## First boot and firmware

1. **Read UEFI first.** With KVM/display and keyboard connected, power on and press **Esc** at the NVIDIA splash. Record the firmware version. NVIDIA's guide treats **36.x** as ready for JetPack 6; if the version is unusual, verify compatibility before writing firmware.
2. **Older-than-36 firmware only:** write the updated 5.1.3 image, boot it, complete Ubuntu setup and connect internet. Before package/updater commands, use `findmnt /` and `lsblk -f` to confirm the root filesystem is on the intended microSD, not the attached NVMe. Check `sudo systemctl status nv-l4t-bootloader-config`; successful scheduling ends inactive with a successful exit. Reboot and allow the firmware update to finish.
3. **Bridge continued:** run `sudo nvbootctrl dump-slots-info`; NVIDIA's expected bridge version is **35.5.0**. Then run `sudo apt update` and `sudo apt install nvidia-l4t-jetson-orin-nano-qspi-updater`. Reboot and observe the QSPI update. Once it completes, power off and switch/rewrite the card to 6.2.1. The 5.1.3 card is no longer the target boot media.
4. **Boot 6.2.1:** insert the written microSD, power on, review the licence and finish language, keyboard, time zone, network and account setup. Suggested hostname: `orin-arm`; record the actual username. Verify `findmnt /` and `lsblk -f` show the intended microSD root before any APT upgrade. If another drive boots, inspect the UEFI boot selection rather than formatting the SSD.
5. Check `sudo systemctl status nv-l4t-bootloader-config` again. If it scheduled another firmware update, reboot and let it finish. Record `sudo nvbootctrl dump-slots-info` and `/etc/nv_tegra_release` afterward.

**Never interrupt power during a firmware update.** If the display remains black or reaches a UEFI shell, inspect the firmware/image pairing rather than repeatedly retrying an incompatible image. Keep the KVM available through firmware updates.

## Update to JetPack 6.2.3

After a healthy 6.2.1 boot, follow the [NVIDIA release instructions](https://forums.developer.nvidia.com/t/jetpack-6-2-3-jetson-linux-36-5-2-is-now-live/379872). Inspect `/etc/apt/sources.list.d/nvidia-l4t-apt-source.list` and change its NVIDIA repository versions to **`r36.5`** (Orin uses `common` and `t234`). Run `sudo apt update`, review the proposed upgrade, then `sudo apt dist-upgrade`; the release instructions also specify `sudo apt install --fix-broken -o Dpkg::Options::="--force-overwrite"` before reboot. Resolve unexpected errors before proceeding.

This updates the BSP and potentially QSPI, not just applications. Do not mix NVIDIA release repositories, APT-upgrade 35.x directly to 36.x, or substitute Ubuntu's `do-release-upgrade`. After reboot, verify the actual L4T version; the `r36.5` repository supplies its current point release, so do not infer the installed version from the command alone.

## SSH and runtime handoff

1. On the Jetson, install `openssh-server`, enable it with `sudo systemctl enable --now ssh`, and record `hostname -I`.
2. From the Mac, connect using the recorded username/IP; verify the host key via the local console before trusting it. Install the intended public SSH key and verify a second login with that key. LAN SSH is enough to start; join the existing Tailscale network when remote station access is needed.
3. Record `uname -a`, `cat /etc/nv_tegra_release`, `cat /etc/os-release`, `python3 --version`, `findmnt /`, `lsblk -f` and `df -h /`. Confirm the root filesystem is on microSD. Inspect the SSD without modifying it.
4. Keep the **default power mode** for first boot; record `sudo nvpmodel -q`. MAXN/SUPER and clock changes wait until cooling, power and workload temperatures have been verified.
5. Follow [jetson-end-to-end.md](jetson-end-to-end.md). The project's current NumPy pin requires **Python 3.12+** even though the SDK declares 3.10+. Use an isolated **Python 3.12** environment; keep Ubuntu's system Python intact. The pickup client also requires SciPy. No local model training, PyTorch or CUDA application stack is needed to bring up the controller.

## Board facts — fill from the device

| Field | Value |
| --- | --- |
| Model / RAM | Orin Nano reported; exact module and RAM unverified |
| Firmware before / after | Unknown |
| JetPack / Jetson Linux / Ubuntu | Not yet verified |
| System Python / arm environment Python | Not yet verified |
| Boot drive | microSD selected; actual root device unverified |
| SSD | 1 TB attached, user reported; contents and format unknown |
| Hostname / username / LAN or Tailscale address | Not yet configured or verified |
| Power mode / fan / temperatures | Not yet verified |
| SSH key login / offline arm tests | Not yet verified |

Do not put passwords, private keys or API tokens in this table or Git. The basic controller does not require a Dream API key; supply credentials separately only when integrating that service.

## Official reference table

| Resource | Grounding |
| --- | --- |
| [NVIDIA hardware layout](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/hardware_layout.html) | microSD/NVMe ports, DisplayPort-only output, DP-to-HDMI support, USB-C limitations |
| [NVIDIA JetPack 6 firmware path](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/update_firmware.html) | firmware inspection, 5.1.3 bridge, QSPI updater, first-boot checks, 19 V supply |
| [JetPack 5.1.3 release](https://developer.nvidia.com/embedded/jetpack-sdk-513) · [bridge SD ZIP](https://developer.nvidia.com/downloads/embedded/l4t/r35_release_v5.0/jp513-orin-nano-sd-card-image.zip) | updated download must resolve to `JP513-orin-nano-sd-card-image_b29.zip` |
| [JetPack 6.2.1 release](https://developer.nvidia.com/embedded/jetpack-sdk-621) · [6.2.1 SD ZIP](https://developer.nvidia.com/downloads/embedded/L4T/r36_Release_v4.4/jp62-r1-orin-nano-sd-card-image.zip) | selected boot image |
| [36.4.4 release hashes](https://developer.download.nvidia.com/embedded/L4T/r36_Release_v4.4/release/release_sha_hashes.txt) | BSP checksums; does not list the SD image ZIP |
| [JetPack 6.2.3 release](https://developer.nvidia.com/embedded/jetpack-sdk-623) · [APT upgrade instructions](https://forums.developer.nvidia.com/t/jetpack-6-2-3-jetson-linux-36-5-2-is-now-live/379872) | explicit 6.2.1 SD to 6.2.3 route |
| [NVIDIA package update guide](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/SoftwarePackagesAndTheUpdateMechanism.html) | consistent firmware/kernel/userspace releases and repository selection |
| [NVIDIA power and thermal guide](https://docs.nvidia.com/jetson/archives/r36.4.4/DeveloperGuide/SD/PlatformPowerAndPerformance/JetsonOrinNanoSeriesJetsonOrinNxSeriesAndJetsonAgxOrinSeries.html) | query power mode, fan management and thermal behavior |

The selected route above is microSD with JetPack 6; a newer JetPack ISO workflow is a separate installation decision, not a prerequisite for this task.
