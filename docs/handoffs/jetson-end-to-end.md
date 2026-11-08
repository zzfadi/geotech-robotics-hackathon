# Handoff: Jetson robot server, end to end

**Owner: FADI, working on the Mac from this repo** (with whichever agents run there). Written Saturday night, Nov 7, from [the team plan](../plan.md), the [Table 10 connection plan](../table10-sdk-connection.md), the [arm bench handoff](../../arm/README.md) and the Slack sources summarized in [the Saturday log](../slack-log-2026-11-07.md). Nothing in this brief has been run on a Jetson.

## Goal

An NVIDIA Jetson (Orin preferred; a Jetson Nano was the board named on Nov 7) becomes the station's robot server. It owns both Maker Arms' CAN adapters, runs the SDK and our holding controller, reads the robot cameras, and reports measured arm state to the rest of the stack. Codex drives the arm through it, the way it drove the right arm from a laptop on Saturday, and the Dream API stays in use alongside it. The Jetson only owns the hardware and the guards; whichever brain sends a move goes through the same controller and the same hold.

End to end means: command in → Jetson controller → arm moves → measured state out to Luna2/Unreal, with a working hold (pause) and a verified return to rest.

## Why it matters for Sunday

Today the arm runs from a laptop over USB. A Jetson at the station frees the laptop for the pitch, gives the demo one fixed robot host, and is where a small trained model can run next to the arm (see [the training brief](small-model-training.md)). It is **not** required for the "hold my beer" MVP: if it isn't solid by late morning, demo from the laptop setup that already works.

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

1. **Identify the board.** Record the exact Jetson model, JetPack/L4T version, Ubuntu version, Python version (SDK needs 3.10+), free disk and its Tailscale hostname. Write them into this file.
2. **Base environment.** Install `libusb-1.0` and `uv` with the normal package manager; create `arm/.venv` with Python 3.12 if available (3.10+ otherwise) and install `arm/requirements.txt`. Note that `arm/requirements-vision.txt` pins `opencv-python-headless`, which may have no aarch64 wheel; use JetPack's OpenCV if so and record the change.
3. **USB access.** Add a udev rule for the gs_usb VID/PID so the adapters open without running the tools as root. Do not use `sudo` to paper over a USB error.
4. **Offline checks first.** `bash arm/tools/arm --help`, `bash arm/tools/arm test`. These don't touch hardware.
5. **Read-only hardware.** With the arms powered by the station operator, `scan` and `watch` both adapters; confirm left/right by adapter serial. One CAN owner at a time: stop any laptop session first.
6. **Cameras on Linux.** `arm/tools/camera_inspect` is a macOS Swift helper. Add a Linux path (V4L2 via OpenCV) that returns the same 640×480 frames from the three robot UVC cameras.
7. **Controller.** Run `pickup_server.py` on the Jetson, bound to localhost. Codex reaches it over an SSH tunnel from the Mac, never over an open port. Replay Saturday's right-arm pickup as the acceptance test. When Dream-1 drives the real arm, its returned targets go through this same controller and its limits, never straight to the motors.
8. **Telemetry out.** Feed measured joint state to the Luna2 relay over Tailscale using the existing Table 10 contract (one arm per session today). Measure feedback age at the relay.
9. **Hold in.** Add a `hold` path to the controller: freeze at the measured pose (no power-off; the arms have no brakes). Test it with hardware output disabled, then in a dry run. It is a software interlock, not a safety stop.
10. **Return and shutdown.** Verify `rest` returns both arms to the verified rest pose before torque release.

Powered steps (5, 7, 9, 10) only with the station attended and the operator on the e-stop.

## What done looks like

- Board facts recorded here.
- `arm test` passes on the Jetson; `scan`/`watch` show fresh feedback on all 14 motors with no faults.
- Saturday's right-arm pickup replays from the Jetson, driven by Codex over the tunnel, and returns to rest.
- Measured state reaches the Luna2 relay with its age reported.
- A `hold` message freezes the arm mid-move and `clear` resumes it, shown in a dry run.
- A dashboard update in `dashboard/updates/` (workstream `arm-station`) with the evidence.

## Open questions

- Which board is it: Orin (Nano/NX/AGX) or the older Nano? The older Nano ships Python 3.6 on JetPack 4, which the SDK does not support; that changes step 2.
- Does the Jetson stay at the station all day Sunday, and who powers the arms for the first read-only test?
- Should the left arm be in scope? Its Saturday attempt missed high with different claw offsets.
- Does the hold interlock stay in the MVP? See the plan's open questions.

## What needs the Mac or hardware

- The Jetson itself, both arms, both CAN adapters, the USB hub and cameras, and the station operator.
- The Mac Mini to flash or image the board if needed (an Orin Nano dev kit can be imaged from an SD card; NVIDIA SDK Manager needs an Ubuntu x86 host, not macOS), to SSH in, and to run Codex.
- Tailscale access to Luna2 for telemetry.
