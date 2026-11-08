# Two Maker Arms: bench handoff

**Latest physical control/vision record:** [November 7 exploration](docs/control-and-vision.md).
After the operator resumed supervised testing, both arms and all three USB cameras
were inspected, the laptop camera was captured, and a small right-wrist roll and
return completed. Joint-reference discrepancies and USB connectivity remain
unresolved; whole-arm control and Dream hardware integration are not verified.

The original gripper-only handoff below records the earlier pause before Dream
testing. Its historical results remain unchanged. Do not run hardware commands
as part of offline setup. This is experimental bench bring-up, not an
unattended-ready controller or Dream/Portal adapter.

## New laptop: offline setup first

From the repository root, with Python 3.12 and [uv](https://docs.astral.sh/uv/)
already installed:

```sh
# Tested on Apple Silicon macOS; install the USB library, not a serial driver.
brew install libusb
uv venv --python 3.12 arm/.venv
uv pip install --python arm/.venv/bin/python -r arm/requirements.txt
bash arm/tools/arm --help
bash arm/tools/arm scan --help
bash arm/tools/arm watch --help
bash arm/tools/arm claw_probe --help
bash arm/tools/arm test
```

These commands do not enumerate adapters, connect arms or enable motors. The
launcher locates its own `arm/.venv`; it does not use another session's environment.
`ARMCTL_PYTHON` may select an existing Python environment. On macOS the launcher
discovers Homebrew's libusb prefix; without Homebrew, arrange libusb discovery
before launching (for example `DYLD_FALLBACK_LIBRARY_PATH`).

The SDK is a dependency fetched at
[`0245316c05cb3e8d900f35e70c08e2e18e36731e`](https://github.com/makermods-robotics/maker-arm-sdk/tree/0245316c05cb3e8d900f35e70c08e2e18e36731e),
not a vendored clone. Package versions and tested host are in
[requirements.txt](requirements.txt) and [provenance.json](provenance.json).
Upstream is [Apache-2.0 licensed](https://github.com/makermods-robotics/maker-arm-sdk/blob/0245316c05cb3e8d900f35e70c08e2e18e36731e/LICENSE).
Its profile remains unmodified. Our installation uses the pinned source normally;
the original bench installed that same clean source editable with dev extras.

Only the stated Apple Silicon macOS setup was live-tested. Linux would need its
normal libusb installation/USB access permissions and Python environment;
Windows and other adapter/OS combinations are not validated by this handoff.
Do not use `sudo` as a substitute for understanding USB errors.

## Rig identity and connections

Two **MakerMods Maker Arm v1** arms use CAN at **1 Mbps** and RobStride **MIT**
protocol. IDs 1-6 are `shoulder_pan`, `shoulder_lift`, `elbow_flex`, `wrist_flex`,
`wrist_yaw`, `wrist_roll`; ID 7 is the RS00 gripper.

Both tested adapters are candleLight/gs_usb, VID `0x1d50`, PID `0x606f`.
They are **not serial SLCAN**: the absence of `/dev/cu.usbmodem*` was expected.
Power, CAN wiring/termination and the physical emergency cutoff must be checked
by the station operator against the manufacturer's instructions before resuming.

| Tested label | Adapter serial | Observed adapter |
| --- | --- | --- |
| right | `0027002E4146570520313238` | bytewerk candleLight, 48 MHz |
| left | `2077378045465008` | ElmueSoft CANable, 160 MHz, CAN-FD capable |

[arm_names.json](tools/arm_names.json) preserves this rig's labels, **not universal
left/right identities**. Select by full serial; USB bus/address can change. Label
the physical adapter and corresponding arm before power cycling or motion.
For another rig, edit the serial-to-name JSON after confirming identities.
Do not infer identity from a USB port or move a joint by hand merely to identify it.

## Read-only inspection, only after the hardware pause is lifted

The following commands access hardware and transmit queries, even without torque
enable. They are not part of the offline quick start:

```sh
bash arm/tools/arm scan
bash arm/tools/arm watch --seconds 5 --plain
```

`scan` queries every candleLight adapter for fault/state replies from IDs 1-7.
`watch` connects every mapped arm, reads angles, reports missing feedback and
disconnects on exit. Neither deliberately enables or releases torque; they cannot
prove an already-running motor is disabled or provide a physical e-stop.
Ctrl-C stops the monitor. Do not run it alongside a motion tool.

`claw_probe` without `--move` is a single-gripper preflight, not passive listening.
Unsupported typed parameters require explicit `--operator-cutoff` even for that
preflight. It never enables by default, but still queries hardware. Never treat
the returned legacy mode zero as independently verified torque-off.

## Supervised opening, not authorized during the current pause

Before any later motion: confirm the correct arm/adapter, secure the arms and
work area, remove people from the travel envelope, and keep a supervising
operator at the physical power switch/e-stop. No verified watchdog exists on
these grippers. A CLI flag records consent; it does **not** verify those conditions.
One arm at a time; never start a whole-arm controller.

After explicit station/operator approval to resume, the final opening command is:

```sh
# Choose ONE full serial; this example selects the tested left adapter.
mkdir -p arm/results
bash arm/tools/arm claw_probe 2077378045465008 \
  --open-only --move --operator-cutoff --output arm/results/left-open.json
```

The equivalent tested-right serial is `0027002E4146570520313238`.
The final code revision was live-tested on **left only**, not rerun on right.
Do not run either command now. A no-`--move` preflight must not be confused with
authorization to follow it with motion. Do not retry an abort or increase guards
to force completion; inspect the result and consult the operator.
The historical small open/close test remains in the source, but **no complete
closing cycle was demonstrated** and it is not the handoff's recommended test.

Final `--open-only` configuration:

| Control/guard | Value |
| --- | --- |
| Enabled motor | 7 only; gravity off, `hold_on_fault=False` |
| Gains / command speed | Kp 10, Kd 2, 3 degrees/s; 25 Hz configuration |
| Target | Vendor lower endpoint -96 degrees; following error bounded to 2.5 degrees |
| Arrival | Within 2 degrees, speed below 5 degrees/s for 0.3 s; not hard-stop contact |
| Measured effort / speed | Stop above 0.65 Nm absolute / 20 degrees/s absolute |
| Feedback | 150 ms stale limit; finite position, velocity, torque and temperature |
| Other guards | Position envelope (1.5-degree margin), warning/fault/mode checks, below 45 C |
| Stall / deadline | 0.25-degree progress within 3 s; distance/command speed + 8 s deadline |
| Release | Torque-off cleanup on attempted enable, including failures |

Open-only disables the gripping/hold regulator to avoid closed-limit clamping at
startup. Neither the effort nor speed guard was raised to bypass the earlier
abort. Guards run in software and cannot guarantee stopping on host/USB failure.

## What actually happened

| Attempt | Settings | Observed outcome |
| --- | --- | --- |
| Small opening | Kp 5, Kd 0.5, 3 degrees/s, 5-degree step | First moved about 2.3 degrees but missed target. Repeated opening moved about 1.7 degrees and aborted on the conservative speed/effort check. No closing after aborts. |
| Right full-open | Kp 10, Kd 1, 5 degrees/s | Success at -94.3683 degrees; earlier controller revision. |
| Left full-open | Kp 10, Kd 1, 5 degrees/s | Reached about -22.4 degrees, then speed guard tripped at -26.3 degrees/s. Peak absolute torque about 0.373 Nm: not overcurrent or proof of an obstruction. |
| Left preflight | Final damped revision | Blocked before enable. A fresh stationary read was fault-free; raw velocity had low-degree/s jitter with a constant encoder position. Cause of that blocked preflight is not established. |
| Left damped full-open | Kp 10, Kd 2, 3 degrees/s | Success at -94.7639 degrees. Final revision, left only. |

Four original captured JSONs, including failures, are retained in
[evidence/](evidence/). They record sampled feedback, requested/completed state
and cleanup acknowledgment. Earlier logs do not record every guard/gain/error
string; the table and provenance preserve the coordinator's run context.
Do not assign the final gains to older successful evidence.
SHA-256 hashes in the provenance manifest cover the byte-for-byte retained logs.

After both torque-off acknowledgments the final read-only gripper readings were
right -94.3 and left -94.6 degrees. Approximate IDs 1-6 angles were right
`[7.0, -0.5, 10.4, -0.7, 2.9, 7.4]`, left
`[1.0, 0.4, -0.0, 0.8, 12.6, -1.9]` degrees. Those were readings, **not powered
lifting/wrist movements**. The user observed claw movement. The monitor/controller
was stopped; both claws were open at the pause boundary. These are historical
observations, not a remote assertion of the current powered state.

## Transport and protocol lessons

- Official SDK backends did not directly support these dongles.
  [gsusb_backend.py](tools/gsusb_backend.py) adapts python-can `GsUsbBus` to the
  SDK `CanBackend`, filters TX echoes, error frames and remote frames, and selects
  serial before resolving the transient USB bus/address.
- On the tested Mac, upstream `GsUsb.start()` reset the USB device, then libusb
  reported a phantom kernel-driver claim and `USBError 13: Access denied`.
  The macOS-only `_start_no_reset` compatibility patch skips reset/detach and
  sends START directly. It worked without sudo with the pinned dependencies.
  This is not blanket support for all platforms/adapters; review private API
  changes before upgrading `gs-usb`.
- CAN echo/ACK proves neither protocol compatibility nor valid motor feedback.
  Left initially answered RobStride private protocol, not MIT, despite ACKs.
  With explicit user approval, IDs 1-7 were switched to MIT through upstream
  [`tools/experimental/lerobot/switch_protocol.py`](https://github.com/makermods-robotics/maker-arm-sdk/blob/0245316c05cb3e8d900f35e70c08e2e18e36731e/tools/experimental/lerobot/switch_protocol.py),
  then that arm was power cycled. Consult its source and
  [tools README](https://github.com/makermods-robotics/maker-arm-sdk/blob/0245316c05cb3e8d900f35e70c08e2e18e36731e/tools/experimental/lerobot/README.md).
  The script does not provide a ready-to-run gs_usb command here. Setup never
  switches protocols. Any future switch needs explicit approval, correct labels
  and power cycling of the correct arm; cycling the other arm caused confusion.
- A whole-turn encoder wrap after power cycling can show +358.4 degrees for about
  -1.6 degrees. `Arm.connect()` applies whole-turn correction. Do not zero or
  auto-calibrate joints to remove this; SDK motor coordinates are not URDF angles.

## Safety limits and troubleshooting

Neither gripper answered the typed-parameter probe needed to verify the
motor-side CAN watchdog. Successful runs explicitly used operator-cutoff mode.
**Do not claim a verified 500 ms watchdog, automatic host-failure stop, or
remotely verified disabled mode.** Legacy firmware acknowledged torque-off
commands but cannot reliably report enabled/disabled mode. Joints 1-6 also lack
a motor watchdog in this SDK. Software stop is not a physical e-stop.

Missing adapters: check libusb discovery/access and the adapter type, not serial
ports. No MIT replies: check physical power/wiring/1 Mbps and protocol with the
operator; don't silently switch protocols. Missing/stale/faulty/warm feedback or
an aborted motion: stop, retain the JSON, use physical cutoff if release is
unconfirmed, and do not force, re-zero, or increase limits. Duplicate labels or
ambiguous serial suffixes are errors; use a full serial.

## Original pause boundary before Dream (earlier handoff)

No complete close cycle, up/down movement, whole-arm powered controller, browser
control panel, camera feedback loop or Dream application integration is verified.
The Portal example targets Metal, not a demonstrated Maker Arm driver.
Read [robot-stack-contract.md](../docs/robot-stack-contract.md) for the existing
policy/camera/freshness/cancellation boundary, and
[maker-arm-model.md](../docs/maker-arm-model.md) for the separate kinematic model.
Keep SDK motor radians separate from URDF radians and uncalibrated jaw gap.
The later supervised wrist-only work is recorded in the exploration linked above.
The Dream application's hardware/stop contract still requires verification before
policy-driven physical control.

## Offline handoff verification

The portable setup installed all 15 pinned packages in a fresh Python 3.12.15
environment. The original 36 mocked claw tests plus 18 portability checks passed
(54 total), including help-before-hardware, invalid duration rejection,
unambiguous serial selection and RX echo/error/remote filtering. All launcher
help commands, dependency compatibility and shell syntax checks passed without
accessing adapters.

The final `claw_probe.py` and its original tests are unchanged from the supplied
working version. Packaging changes remove session/Homebrew absolute-path
assumptions, scope the USB workaround to macOS, reject ambiguous identities,
add safe scan help, validate monitor duration, clean up failed connections and
make scan errors return nonzero. These changes have **offline validation only**;
they are not additional hardware trials.
