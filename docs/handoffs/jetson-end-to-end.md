# Handoff: Jetson robot server, end to end

**Owner: FADI, working on the Mac Mini from this repo** (with whichever agents run there). Written Saturday night, Nov 7, from [the team plan](../plan.md), the [Table 10 connection plan](../table10-sdk-connection.md), the [arm bench handoff](../../arm/README.md) and the Slack sources summarized in [the Saturday log](../slack-log-2026-11-07.md). Updated Nov 8 with installer recovery and network evidence. The isolated runtime and offline tests pass on the Jetson; controller and arm hardware acceptance remain pending.

## Goal

An NVIDIA **Jetson Orin Nano** becomes the station's robot server. Bootloader repair, OEM finalization and graphical account setup are complete; **`fadi@orin-arm`** reached the desktop after reboot. The running root is **`/dev/nvme0n1p1`** on the **SHPP41-1000GM, 931.5G** NVMe. Dedicated-key SSH to **`fadi@192.168.8.136`** succeeded with strict host-key verification against the console. Actual versions are **Ubuntu 24.04.4**, **L4T 39.2.1**, **UEFI 39.2.1-gcid-46758480**, **kernel 6.8.12-1021-tegra** and **system Python 3.12.3**, with **7.3 GiB RAM**. The isolated Python **3.12.15** runtime passes **18-package dependency compatibility**, missing-library checks and **140 offline arm tests in 2.77 seconds**; both pickup help commands pass without starting a controller or hardware session. The three failed global DHCP/DNS services were stopped and disabled, leaving zero failed units and system state `running`. Follow [the setup handoff](orin-nano-setup.md) for recovery evidence and current network state. Tailscale is installed with its daemon active, but remains `NeedsLogin` without enrollment while a router-based remote-access alternative is reviewed.

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

0. **Installed OS and SSH verified.** [orin-nano-setup.md](orin-nano-setup.md) records completed setup, the running NVMe root and authenticated dedicated-key SSH with console-verified host identity. Use the Mac alias `ssh orin-arm` for `fadi@192.168.8.136`, with the dedicated key and persistent console-verified known-hosts entry. Enabled/active `ssh.socket` provides SSH activation; disabled `ssh.service` is expected. The failed global DHCP/DNS services have been stopped and disabled individually, while NVIDIA USB networking and the normal network/resolver services remain active. Keep the unchanged default 25 W / mode 1 setting; observed 47–49°C is only an idle baseline.
1. **Retain board facts.** The [setup board-facts table](orin-nano-setup.md#board-facts--update-from-observed-results) now records model/RAM, firmware, L4T/Ubuntu, root device, system Python, free disk and network identity. The isolated runtime is Python 3.12.15; record fan/workload cooling observations during later attended tests. No Jetson arm result is established by the existing macOS bench evidence.
2. **Checkout and base environment verified.** The Jetson checkout at **`d5c4f8d6ca67b4d3cc1a6eeb4eb22a1fce704542`** uses **`/home/fadi/jetson-runtime/.venv`**, running **Python 3.12.15**. The pinned SDK built successfully; `uv pip check` reports **18 compatible packages**, with no missing libraries. The corrected build closure includes packaging 26.3, setuptools 84.2.1 and wheel 0.48.0. Before later changes, read `AGENTS.md`, preserve local work, synchronize clean `main` with `git pull --ff-only origin main`, and compare requirements with the cache. Set `ARMCTL_PYTHON=/home/fadi/jetson-runtime/.venv/bin/python` to select the installed environment; keep system Python intact. The base environment includes SciPy for pickup-client imports. OpenCV and the three-camera workflow are separate: verify an ARM64/Python 3.12-compatible stack before using `arm/requirements-vision.txt`. No PyTorch, local model or GPU application stack is required for the controller.
3. **USB access.** Add a udev rule for the gs_usb VID/PID so the adapters open without running the tools as root. Do not use `sudo` to paper over a USB error.
4. **Offline checks passed.** The Jetson ran **140 offline arm tests in 2.77 seconds**; both `pickup_client.py --help` and `pickup_server.py --help` passed. No controller or hardware session was started. Results are in target `PROVISIONING-RESULT.json` and `/home/fadi/jetson-prep-assets/provisioning-resume-final.log`; both have privately preserved copies with matching target/local/external SHA-256 values in the [setup evidence record](orin-nano-setup.md#installed-runtime-and-offline-validation). Repeat affected checks after code/dependency changes with the verified isolated interpreter before attaching CAN adapters.
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

- Installed OS, account, running NVMe root and dedicated-key SSH are verified; isolated runtime and 140 offline arm tests now pass. Controller/hardware acceptance is still pending. Service health now reports zero failed units and `running`. Tailscale is installed but unauthenticated while the user’s router-based remote-access alternative is reviewed; the Mac’s private network has no observed Luna2 peer. Architecture, enrollment and route verification remain pending. The failure/repair/finalization archives remain private. KVM still uses Mac sharing at 192.168.2.2; its reserved Beryl address is 192.168.8.166, but the cable move/access is pending. Same-Mac cloud P2P console is verified with no HDMI; cloud-route keyboard, complete portable LAN and off-site access remain unverified.
- Does the Jetson stay at the station all day Sunday, and who powers the arms for the first read-only test?
- Should the left arm be in scope? Its Saturday attempt missed high with different claw offsets.
- Does the hold interlock stay in the MVP? See the plan's open questions.

## What needs the Mac or hardware

- The Jetson itself, both arms, both CAN adapters, the USB hub and cameras, and the station operator.
- The Mac and authorized 16 GB SanDisk USB to write and validate the current ISO, a DisplayPort monitor and USB keyboard for installation, then SSH and Codex. Prepared downloads belong under `/Volumes/Fadi/Jetson-Orin-Nano-Setup-2026-11-07`; check completion and integrity before use. The [setup handoff](orin-nano-setup.md) contains official image, firmware, KVM and power references.
- Tailscale access to Luna2 for telemetry. Tailscale 1.104.5 and archive keyring 1.35.181 are installed after repeated hash/GPG verification. The daemon is active/enabled but reports `NeedsLogin`, with no addresses or enrollment. The [Beryl LAN subnet-gateway recommendation](orin-nano-setup.md#remote-access-recommendation--awaiting-selection) is awaiting selection: distinct kit LAN, LAN-only routes, intended team network and policy/route approval. No enrollment or route changes have been made. Verify actual Luna2 access before treating telemetry as ready. The preserved cache does not bundle Ubuntu dependency packages; see the setup handoff.
