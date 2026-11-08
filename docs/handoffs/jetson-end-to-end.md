# Handoff: Jetson robot server, end to end

**Owner: FADI, working on the Mac Mini from this repo** (with whichever agents run there). Written Saturday night, Nov 7, from [the team plan](../plan.md), the [Table 10 connection plan](../table10-sdk-connection.md), the [arm bench handoff](../../arm/README.md) and the Slack sources summarized in [the Saturday log](../slack-log-2026-11-07.md). Updated Nov 8 with installer recovery and network evidence. The controller and arm acceptance steps below have not been run on the Jetson.

## Goal

An NVIDIA **Jetson Orin Nano** becomes the station's robot server. UEFI **39.2.1-gcid-46758480**, bootloader package repair and OEM finalization are verified. After reboot, Ubuntu graphical Welcome appeared at **02:25:35 Pacific**; NVIDIA license acceptance is complete and local-account setup remains pending. The NVMe is **SHPP41-1000GM, 931.5G**. The address `192.168.8.136` responds to ping/TCP22, but SSH host identity/login, actual running root and OS/runtime checks remain unverified. Follow [the setup handoff](orin-nano-setup.md) for the preserved failure evidence and current setup state. Tailscale is cached only.

The intended final Jetson owns both Maker Arms' CAN adapters, runs the SDK and holding controller, reads the robot cameras, and reports measured arm state. Codex and the Dream API send moves through the same controller and guards.

End to end means: command in → Jetson controller → arm moves → measured state out to Luna2/Unreal, with a working hold (pause) and a verified return to rest.

## Why it matters for Sunday

Today the arm runs from a laptop over USB. A Jetson at the station frees the laptop for the pitch and gives the demo one fixed robot host. Local model training/inference is optional later work ([edge model brief](edge-model.md)); it is not a dependency of controller setup. The Jetson itself is **not** required for the "hold my beer" MVP: if it isn't solid by late morning, demo from the laptop setup that already works.

Dream's own advice for the loop that runs here (320×176 frames, hybrid async/sync on an 850 ms round-trip median, a 0.85 m/s speed clamp, `dream.check`, the camera checklist, TCP between the fingertips) is in [the workshop takeaways](dream-workshop-takeaways.md).

## Inputs and where they live

| Input | Where |
| --- | --- |
| Arm SDK (pinned commit), dependency versions | `arm/requirements.txt`, `arm/provenance.json` |
| Rig identity: two Maker Arm v1, CAN 1 Mbps, RobStride MIT, IDs 1–7, candleLight/gs_usb adapters (VID `0x1d50`, PID `0x606f`) with their serials | `arm/README.md`, `arm/tools/arm_names.json` |
| Holding controller over a localhost socket (`move`, `grip`, `status`, `clear`, `rest`) and the IK client used for Saturday's pickup | `arm/tools/pickup_server.py`, `arm/tools/pickup_client.py` |
| Saturday's right-arm pickup evidence | `arm/evidence/2026-11-07-cylinder-pickup.json`, `arm/README.md` |
| Read-only scan, watch and diagnosis tools | `arm/tools/scan_arms.py`, `watch_arms.py`, `diagnose_arms.py` |
| Telemetry relay and contact contract (Table 10) | `Interaction/README.md`, `Interaction/telemetry.py`, `Interaction/sdk_adapter.py`, `docs/table10-sdk-connection.md` |
| Hold interlock design (Isaac → LiveKit "hold" message) | `docs/plan.md`, `docs/slack-log-2026-11-07.md` (decision 2) |
| URDF and SDK-to-URDF mapping status | `docs/maker-arm-model.md`, `Interaction/feedback_pose.py` |

Credentials: relay bearer tokens live in ignored files on Luna2 (`Saved/DreamDebugger/…`). Never copy them into the repo, a script or a command line.

## Steps

**Do not start `pickup_server.py` or configure it to auto-start during OS provisioning.** Its startup calls `arm.connect()` and `arm.enable()` **before** binding the localhost server, so starting it can enable motors before any client connects or the port is ready. Keep CAN adapters disconnected during provisioning and run the offline wrapper tests and help commands in step 4 first. Start the controller only for the attended hardware stage with the operator ready; see [the startup implementation](../../arm/tools/pickup_server.py).

0. **Complete first-boot setup and verify persistent SSH.** Follow [orin-nano-setup.md](orin-nano-setup.md): graphical Welcome has been observed after the repaired installation's OEM finalization and reboot. Complete the remaining time-zone/account setup, then verify the running OS, `findmnt /`, expected NVMe and authenticated SSH host identity/login. Ping and an open TCP22 port are not sufficient. Temporary installer SSH was for recovery only. Keep default power mode until cooling and temperatures are checked.
1. **Identify the board.** Fill the [setup board-facts table](orin-nano-setup.md#board-facts--update-from-observed-results) with actual model/RAM, firmware, JetPack/L4T, Ubuntu, root device, Python versions, free disk and network identity. No Jetson result is established by the existing macOS bench evidence.
2. **Checkout and base environment.** Install Linux `git` and `libusb-1.0-0`, then clone `https://github.com/zzfadi/geotech-robotics-hackathon.git` and `cd geotech-robotics-hackathon`. Verify `git branch --show-current` is `main`, record `git rev-parse HEAD`, and read `AGENTS.md`. If a checkout already exists, preserve its work and synchronize clean `main` with `git pull --ff-only origin main`. Obtain `uv`; create `arm/.venv` with **Python 3.12** and install `arm/requirements.txt`. The SDK declares 3.10+, but NumPy 2.5.4 requires 3.12+; do not replace system Python. The pickup client imports SciPy, so ensure the requirements include it. OpenCV is separate camera/vision work: verify an aarch64 wheel before using `arm/requirements-vision.txt`. Ubuntu/JetPack OpenCV built for Python 3.10 is not automatically usable from the isolated 3.12 environment. No PyTorch, local model or GPU stack installation is required for the controller.
3. **USB access.** Add a udev rule for the gs_usb VID/PID so the adapters open without running the tools as root. Do not use `sudo` to paper over a USB error.
4. **Offline checks first.** `bash arm/tools/arm --help`, `bash arm/tools/arm test`, and `arm/.venv/bin/python arm/tools/pickup_client.py --help`. These don't touch hardware; record actual results before attaching CAN adapters.
5. **Read-only hardware.** With the arms powered by the station operator, `scan` and `watch` both adapters; confirm left/right by adapter serial. One CAN owner at a time: stop any laptop session first.
6. **Cameras on Linux.** `arm/tools/camera_inspect` is a macOS Swift helper. `vision/run.py` has an OpenCV camera preview, but its JetPack 6 Torch instructions must be rechecked for this JetPack 7.2.1 host. Provide and verify the pickup workflow's equivalent three-UVC-camera 640×480 snapshot path; the preview alone does not establish that contract.
7. **Controller.** Run `pickup_server.py` on the Jetson, bound to localhost. Codex reaches it over an SSH tunnel from the Mac, never over an open port. Replay Saturday's right-arm pickup as the acceptance test. When Dream-1 drives the real arm, its returned targets go through this same controller and its limits, never straight to the motors.
8. **Telemetry out.** Feed measured joint state to the Luna2 relay over Tailscale using the existing Table 10 contract (one arm per session today). Measure feedback age at the relay.
9. **Hold in.** Add a `hold` path to the controller: freeze at the measured pose (no power-off; the arms have no brakes). Test it with hardware output disabled, then in a dry run. It is a software interlock, not a safety stop.
10. **Return and shutdown.** Verify `rest` returns both arms to the verified rest pose before torque release.

Powered steps (5, 7, 9, 10) only with the station attended and the operator on the e-stop.

## What done looks like

- Actual board facts and SSH verification recorded in the setup handoff.
- `arm test` passes on the Jetson; `scan`/`watch` show fresh feedback on all 14 motors with no faults.
- Saturday's right-arm pickup replays from the Jetson, driven by Codex over the tunnel, and returns to rest.
- Measured state reaches the Luna2 relay with its age reported.
- A `hold` message freezes the arm mid-move and `clear` resumes it, shown in a dry run.
- A dashboard update in `dashboard/updates/` (workstream `arm-station`) with the evidence.

## Open questions

- Graphical first boot is observed; time-zone/account setup, actual running root/versions and persistent authenticated SSH remain pending. RAM and the SSD's prior contents are unverified; target identity is 931.5G `SHPP41-1000GM`. The failure/repair/finalization archives remain private. KVM still uses Mac sharing at 192.168.2.2; its reserved Beryl address is 192.168.8.166, but the cable move/access is pending. Same-Mac cloud P2P console is verified with no HDMI; cloud-route keyboard, complete portable LAN and off-site access remain unverified.
- Does the Jetson stay at the station all day Sunday, and who powers the arms for the first read-only test?
- Should the left arm be in scope? Its Saturday attempt missed high with different claw offsets.
- Does the hold interlock stay in the MVP? See the plan's open questions.

## What needs the Mac or hardware

- The Jetson itself, both arms, both CAN adapters, the USB hub and cameras, and the station operator.
- The Mac and authorized 16 GB SanDisk USB to write and validate the current ISO, a DisplayPort monitor and USB keyboard for installation, then SSH and Codex. Prepared downloads belong under `/Volumes/Fadi/Jetson-Orin-Nano-Setup-2026-11-07`; check completion and integrity before use. The [setup handoff](orin-nano-setup.md) contains official image, firmware, KVM and power references.
- Tailscale access to Luna2 for telemetry. An Ubuntu 24.04 ARM64 cache of Tailscale 1.104.5 and its archive keyring is prepared under the staging folder; it has not been installed or activated. Verify the installed host first. Required Ubuntu dependencies are not bundled, package installation can start its daemon, and tailnet authentication/access verification remain separate steps; see the setup handoff.
