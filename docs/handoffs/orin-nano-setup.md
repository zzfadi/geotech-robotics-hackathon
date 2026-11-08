# Jetson Orin Nano setup: flash, first boot, SSH

**For FADI at home, the night before the arm server goes to the station.** The board is a **Jetson Orin Nano Developer Kit** with a 1 TB NVMe SSD on the carrier board. It hasn't been used in a long time, so its firmware may be old. These steps get it onto the current JetPack, booting from the SSD, and reachable over SSH so an agent on the Mac can finish the job in [jetson-end-to-end.md](jetson-end-to-end.md).

Versions checked on Nov 8, 2026 against NVIDIA's [Orin Nano Quick Start](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/quick_start.html), [JetPack 6.x Update Path](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/update_firmware.html) and [JetPack downloads](https://developer.nvidia.com/embedded/jetpack/downloads). Nothing here has been run on this board yet; record what you see in [Board facts](#board-facts).

## Which route

- **Target:** JetPack **7.2.1** (Jetson Linux **r39.2.1**), the latest NVIDIA lists for the Orin Nano dev kit.
- **Install method:** the **Jetson ISO** on a USB stick, installed onto the NVMe SSD. NVIDIA no longer ships an SD card image for this kit, and **SDK Manager needs an Ubuntu x86_64 host**, so it doesn't run on the Mac. The ISO route needs only the Mac to write the stick.
- **Firmware gate:** JetPack 7.2.x needs QSPI firmware **36.x or newer**. A kit still on 35.x firmware (JetPack 5 era) has to go through NVIDIA's JetPack 6.x update path first, which needs a microSD card.

## What you need

- On the Mac: [Balena Etcher](https://etcher.balena.io), about 25 GB free disk, the Jetson ISO `jetsoninstaller-r39.2.1-…-arm64.iso` from the Quick Start or downloads page.
- A **USB stick, 16 GB or more** (it gets erased). Copying the ISO onto it as a file does not work; write it with Etcher.
- The kit's power supply, Ethernet cable (easiest for first network), NVMe SSD already installed.
- **A screen path, one of:** a DisplayPort monitor (or a DP-to-HDMI adapter) plus USB keyboard and mouse, **or** a 3.3 V USB-to-TTL serial cable for headless setup (see [No monitor](#no-monitor)).
- **Only if firmware is 35.x:** a microSD card, 64 GB UHS-1 or better.

## Steps

1. **Check the firmware version.** Power on, press **Esc** at the NVIDIA splash; the UEFI screen shows the firmware version. If the board still boots an old OS, `sudo nvbootctrl dump-slots-info` prints it too. **36.x or newer:** go to step 3. **35.x or older:** step 2.
2. **Old firmware only (35.x): NVIDIA's JetPack 6.x update path.**
   1. Write `JP513-orin-nano-sd-card-image_b29.zip` (JetPack 5.1.3) to the microSD with Etcher, boot from it, finish first-boot setup, connect to the internet.
   2. `sudo systemctl status nv-l4t-bootloader-config` should end inactive with a successful exit. Reboot. `sudo nvbootctrl dump-slots-info` should now show 35.5.0.
   3. `sudo apt update && sudo apt install nvidia-l4t-jetson-orin-nano-qspi-updater`, reboot, let the QSPI update finish.
   4. Power off, write the JetPack 6.2.1 SD image (linked from the update-path page) to the microSD, boot it, check `nv-l4t-bootloader-config` again and reboot if it scheduled another update. Firmware should now read 36.x. Remove the microSD.
3. **Write the ISO to the USB stick** with Etcher on the Mac.
4. **Boot the installer.** USB stick in, power on, **Esc** at the splash → **Boot Manager** → the USB disk. Pick the USB explicitly; a forum user whose prompt timed out booted the wrong drive.
5. **Firmware prompt.** When the installer asks to update QSPI firmware, press **Y within 30 seconds**. It can take two update passes and reboot on its own. If it reboots back past the installer, repeat step 4.
6. **Install to the SSD.** In the GRUB menu choose **Install Jetson ISO r39.2.1**, select the **NVMe** drive (it erases it), confirm, and reboot when told. Pull the USB stick if asked.
7. **First boot.** Accept the EULA, then language, keyboard, time zone, network (plug in Ethernet), and create the user. Suggested: computer name `orin-arm`, user `fadi`. Write both down.
8. **Update and open SSH.** In a terminal on the Jetson:

   ```
   sudo apt update && sudo apt full-upgrade -y
   sudo apt install -y openssh-server
   sudo systemctl enable --now ssh
   hostname -I
   ```

   The last line prints its IP address.
9. **Power mode.** Set **MAXN SUPER** from the power-mode menu in the top bar, or check with `sudo nvpmodel -q`. Some JetPack 7.2 users reported only the 7 W and 15 W modes after upgrading; if that happens, note it here and carry on, it doesn't block the arm server.
10. **Remote access from anywhere.** The station already uses Tailscale for Luna2. Install it on the Jetson with Tailscale's official installer and sign in to the same tailnet:

    ```
    curl -fsSL https://tailscale.com/install.sh | sh
    sudo tailscale up --ssh=false
    tailscale ip -4
    ```

    `--ssh=false` keeps plain OpenSSH as the way in, so key setup below is the one that counts.
11. **SSH key from the Mac.** On the Mac:

    ```
    ssh-copy-id fadi@orin-arm
    ssh fadi@orin-arm 'uname -a; cat /etc/nv_tegra_release; python3 --version'
    ```

    Use the IP from step 8 or the Tailscale name if `orin-arm` doesn't resolve. Once the key works, password login can be turned off later.

## No monitor

NVIDIA's documented headless path is a **USB-to-TTL serial cable** on the carrier board's Button Header: the adapter's TX to **pin 3 (RXD)**, RX to **pin 4 (TXD)**, ground to **pin 7 (GND)**. On the Mac, `ls /dev/cu.usbserial*`, then `screen /dev/cu.usbserial-XXXX 115200`, and press Esc there instead of on a keyboard. The installer and first-boot screens may still expect a display; if the serial console doesn't show them, borrow any monitor or TV with DisplayPort (or a DP-to-HDMI adapter) for steps 4 to 7 only. After step 8 everything is over SSH.

## If 7.2.1 goes wrong

The NVIDIA forum lists 7.2.1 install-failure reports. If the ISO install fails twice or the board won't come back, stop and fall back to **JetPack 6.2.x** (the firmware you'll already have after step 2 or 5): it runs Ubuntu 22.04 with Python 3.10, which the arm SDK accepts. Record which one you ended up on.

## Hand off to the Mac agent

- [ ] Hostname and IP (LAN and Tailscale) written in [Board facts](#board-facts)
- [ ] Username, and `ssh fadi@orin-arm` works from the Mac with the key, no password
- [ ] JetPack / L4T, Ubuntu and Python versions recorded (from step 11's command)
- [ ] Point the agent at [`docs/handoffs/jetson-end-to-end.md`](jetson-end-to-end.md) in `github.com/zzfadi/geotech-robotics-hackathon`, starting at step 1
- [ ] The Dream API key reaches the Jetson only as the `DREAM_API_KEY` environment variable, set in the shell or a local file outside the repo. Never in a commit, script or command line.

## Board facts

| Field | Value |
| --- | --- |
| Model | Jetson Orin Nano Developer Kit |
| Firmware before / after | |
| JetPack / Jetson Linux | |
| Ubuntu / Python | |
| Boot drive | 1 TB NVMe SSD |
| Hostname / user | |
| LAN IP / Tailscale IP | |
| Power mode | |
