# Physical control and camera exploration — November 7, 2026

This is the physical bench record for the supervised exploration starting around
16:15 Pacific. It supplements the earlier gripper-only handoff. The operator
confirmed secured arms, a clear workspace and readiness at the physical cutoff,
then authorized further staged control. No Dream requests or Unreal changes were
made in this exploration.

### Reconnection verification — 16:42 Pacific

Both original adapter serials returned after the operator reconnected the USB
cables. Five sampled rounds from each arm yielded fresh feedback on all 14 motors
and zero reported fault words. The same nominal joint-limit discrepancies remain.
All three robot UVC cameras and the laptop camera returned fresh 640×480 images
through the packaged native helper. Robot camera IDs reverted to `0x311…` after
the USB topology change. A fresh overview confirmed the tabletop layout; the
right wrist view showed a cable crossing immediately in front of the jaws, so
clearance was requested before the larger sweep. No motor was enabled during
this reconnect inspection. See `arm/evidence/2026-11-07-usb-reconnection.json`.

## Verified hardware and software

- Both Maker Arm adapters are accessible by exact serial: left `2077378045465008`,
  right `0027002E4146570520313238`. The left adapter was initially missing and
  appeared after the operator fixed the connection. These are the rig's saved
  labels; another rig must establish its own physical identity.
- All 14 motor IDs replied through the pinned Maker Arm SDK and the local
  `GsUsbBackend`, at 1 Mbps using MIT protocol. Feedback was fresh, reported fault
  words were zero, and measured temperatures were approximately 30–33 °C.
- Python 3.12.15, the 15 pinned packages in `arm/requirements.txt`, and Homebrew
  libusb 1.0.30 were installed/available on this Mac. No SDK upgrade, protocol
  switch, fault reset, encoder zero or profile-limit change was performed.
- None of the 14 motors answered the SDK typed-parameter probe. Firmware versions,
  actual disabled-mode telemetry and motor-side watchdog behavior are unverified.
  A reply to torque-off is an acknowledgment, not independently verified release.

## SDK coordinates and current reference discrepancy

The SDK takes **motor-profile joint radians**, in order: shoulder pan, shoulder
lift, elbow flex, wrist flex, wrist yaw, wrist roll, gripper. `get_joint_positions()`
includes configured direction/offset and the SDK's whole-turn correction. Raw
motor feedback is a separate field. Do not send the Unreal model's URDF angles or
metre-valued jaw gap directly to these motors.

Representative stationary readings after both adapters connected:

| Joint | Left degrees | Right degrees | Nominal SDK bounds, degrees |
|---|---:|---:|---:|
| Shoulder pan | 35.53 | 5.13 | −158.2 … 156.1 |
| Shoulder lift | 0.45 | −0.45 | −175.3 … −3.2 |
| Elbow flex | −0.80 | 10.80 | 2.8 … 236.1 |
| Wrist flex | 0.65 | −1.00 | −65.2 … 104.0 |
| Wrist yaw | 6.80 | 10.32 | −97.6 … 78.0 |
| Wrist roll | −1.64 | 3.02 | −153.2 … 152.0 |
| Gripper | −99.47 | −103.80 | −96.0 … −2.5 |

The right gripper's raw reading was about +256.2°; the SDK subtracts a whole turn
to yield −103.8°. This still lies beyond its nominal opening limit. Both grippers
were visibly open. The shoulder-lift readings and left elbow are also outside
nominal bounds. This might involve the folded rest pose, zero reference or physical
travel; the cause is **not established**. The SDK's 20° full-turn grace can allow
such readings during connection/enable; it does not prove calibration. The new
diagnostic checks nominal limits without that grace. Do not automatically re-zero
or widen bounds to make the report pass.

Read-only sampling also exposed reported velocity with little encoder movement:
30 samples of the left wrist stayed at −1.6379° while velocity ranged −5.079 to
+6.002°/s. The right wrist varied only 0.022° while velocity ranged ±4.156°/s.
These are sampled observations, not proof of zero motion or a firmware diagnosis.
This matters for the stationary preflight; the velocity guard remains intact.

## Actual motion evidence

Only **right motor 6 (wrist roll)** was enabled during this session. No shoulder,
elbow, wrist-flex, wrist-yaw or gripper target was commanded. The left arm was
inspected but not powered by these tests.

1. A 2° sweep at 1°/s with deliberately low Kp/Kd 5/1 moved about 0.22° and hit
   the eight-second completion deadline. The controller stopped, acknowledged
   torque-off, and no return segment was attempted. Peak recorded effort was
   0.120 Nm; the 201 samples had feedback age below 25 ms. The operator inspected
   the wrist and reported no visible obstruction.
2. A comparison using the pinned SDK's wrist gains 30/2 was blocked **before
   enable** by the 5°/s stationary-preflight velocity guard. No guard was relaxed.
3. After renewed authorization and fresh camera inspection, the same guarded
   comparison passed preflight and completed: **3.132° → 4.868° → 3.286°**,
   against a 5.132° outbound target and 3.132° return target. Both settled within
   the declared 0.5° tolerance. Release was acknowledged; legacy mode remains
   unverifiable. This establishes a small closed-loop wrist test, not full-arm
   or Cartesian control.
4. An 8° sweep at 3°/s was prepared, but **did not start** because the overview
   capture readiness check failed. macOS then listed none of the three USB
   cameras. The operator was asked to reconnect the hub.
   A subsequent USB-tree check showed both arm adapters absent as well. No further
   powered test was attempted while the device connections were unavailable.

`wrist_probe` is explicitly bounded to motor 6, no gravity feed-forward, no
full-turn grace, one outbound segment and one return. It retains a 0.4 Nm effort
guard, 15°/s measured-speed guard, 150 ms freshness limit, 45 °C limit, 1° envelope
margin, and 0.5° maximum following error in the final version. Defaults remain
2°/1° per second; CLI hard caps are 10°/5° per second. These caps are experimental
software constraints, not validated safe operating limits. Higher settings have
not been demonstrated by the evidence above. An abort does not trigger retries,
extra force or a return trajectory. Physical cutoff supervision remains required.

## Cameras and what they see

Three identical Sonix/KD-USB UVC cameras enumerate as VID `0x2c7f`, PID `0x4a60`.
They share the same product/serial string, so that string cannot identify roles.
macOS unique IDs incorporate USB location and **changed after recabling**:
`0x311…` became `0x111…`. Recheck the image after any USB topology change; never
silently use an old camera index or role mapping.

The verified four-view snapshot used:

| Camera ID at capture | Visual role | Useful observation |
|---|---|---|
| `0x11130002c7f4a60` | Table overview | Both arm bases/grippers, central work surface and surrounding clutter |
| `0x11110002c7f4a60` | Wrist view A | Open jaws and near-field tabletop, laptop beyond |
| `0x11140002c7f4a60` | Wrist view B | Open jaws and near-field tabletop, laptop/cables beyond |
| `6C707041-05AC-0011-0002-000000000001` | MacBook Pro camera | People and room; robot workspace is outside the current view |

Wrist A/B are **not yet registered to left/right arm serials**. Establish that
mapping with an isolated observed movement or an operator's camera-identification
check. Do not infer it from enumeration order. All four returned 640×480 JPEGs.
The USB devices advertise formats through 1920×1080; format enumeration is not a
throughput test. USB format preference was NV12 at 5 fps. Captures were sequential,
not synchronized multi-view video. Intrinsics, distortion, extrinsics, metric
depth, real exposure timestamps and simultaneous sustained throughput remain open.

A plain ffmpeg capture and a bare Swift permission request initially produced no
frames / timed out. A native app with `NSCameraUsageDescription` successfully
obtained normal macOS permission and captured the cameras. No privacy database or
settings were bypassed. `camera_inspect` reproduces this helper locally.

## Initial scene understanding

This is visual interpretation of timestamped snapshots, **not a calibrated 3-D
world model or collision-free motion certificate**:

- Two mounted arms occupy the near edge of a shared tabletop, with both jaws open.
- The central tabletop patch is comparatively clear in the reviewed overview.
  Cables form loops between/near the arms; tools and cups occupy the right side,
  and supplies occupy the left. Laptops and people are along the far edge.
- The overview provides object/layout context; wrist views provide close gripper
  context. The laptop camera is useful for room/interaction context in its present
  orientation, not table geometry. No person identity is inferred.
- A single image cannot establish depth or the swept volume of an arm. A scene
  record must retain image evidence, sample times, coordinate frames, uncertainty
  and stale-data state. It must not convert a clear-looking image into motion
  authorization.

The smallest useful next world model should contain: two registered arm base
frames; measured joint state with the verified motor-to-URDF mapping; calibrated
camera-to-base transforms; the table plane; measured object poses and uncertainty;
and timestamped occupancy. Register the bases/table with a measured target and
validate held-out points before Cartesian reaching. Existing simulation contact
or hand-tracking results do not establish those physical registrations.

## Reproducible tools

Run from the repository root after the offline setup in `arm/README.md`:

```sh
bash arm/tools/arm test
bash arm/tools/arm diagnose --samples 10 --output arm/local/new-session/arms.json
bash arm/tools/camera_inspect arm/local/new-session/cameras
bash arm/tools/arm wrist_probe right --output arm/local/new-session/wrist-preflight.json
```

`diagnose` never enables torque. It reports expected-but-missing adapters, raw and
SDK-corrected positions, nominal bounds, feedback age, faults, mode support and
samples. Exit 1 means inspection has missing devices, failed reads or nominal
limit issues. The SDK queries faults before state reads; its legacy state-read
frame can clear a fault arising between those two requests, so this is not passive
CAN logging. `wrist_probe` without `--move` also only inspects.

A **supervised, explicitly selected motion** uses `wrist_probe` with `--move
--operator-cutoff --vendor-gains`. The larger staged options are `--step-deg` and
`--speed-deg-s`; they are not permission to escalate after a failed test. Inspect
and retain failures, verify scene clearance and resolve the cause with the operator.
There is no unattended hardware controller in this handoff.

Camera bursts use `bash arm/tools/camera_inspect NEW_DIRECTORY 100 CAMERA_ID`.
They save at most 100 JPEGs at no more than approximately 5 per second, plus sample
PTS and host receipt times. Each run requires a new directory to preserve evidence.
The report identifies missing/failed sources; no cached image masquerades as fresh.
The launcher requires Xcode command-line tools and normal camera permission. It
builds an ad-hoc-signed app under ignored `arm/local/`. Fresh media, runtime reports
and compiled helpers stay local; the compact factual record is in `arm/evidence/`.

The arm launcher now preserves the caller's directory: an output path such as
`arm/local/...` no longer resolves beneath `arm/tools/arm` (which is a file).

## Boundary before Dream-1

The existing `DreamSim` path controls **Unreal**, not this physical rig. Reuse its
observation/action schema and evidence handling only after implementing the physical
contract. Physical Dream integration needs all of the following demonstrated:

- Camera roles and capture freshness; matched joint/image timestamps.
- Calibrated motor-profile radians ↔ URDF/EEF mapping, and gripper motor angle ↔
  jaw opening. The URDF's maximum gap is not a motor-coordinate conversion.
- Repeatable bounded joint moves, held pose and appropriate stop/cancel behavior;
  neither an API cancellation nor stopping target delivery proves a motor stop.
- A registered table/workspace and observed object poses.
- A hardware execution boundary that enforces turn grants, rejects stale queued
  actions and records actual outcomes.

The cameras have now been restored. Next motion work is staged wrist verification
after clearing the visible cable, then
operator/manufacturer-guided reference validation before any lifting-joint or
whole-arm trajectory. No Dream API calls are needed to resolve these dependencies.
