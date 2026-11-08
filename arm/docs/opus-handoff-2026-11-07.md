# Opus handoff — physical Maker Arm bench, November 7, 2026

## Start here

The user requested a wrap-up and handoff to Opus for a different session at about
17:40 Pacific. **Do not automatically resume a powered routine from this file.**
Resume the physical task when the user starts the next session. Their objective
is camera-to-arm alignment, gripper calibration, then picking up the upright
yellow cylinder; Dream-1 integration follows physical control. No pickup has
been attempted and no calibrated camera-to-arm transform exists yet.

The user strongly prefers autonomous progress and no repeated setup questions.
They previously confirmed secured arms, cleared space, and attendance at a
physical cutoff. They added a standard **63 × 88 mm playing card**, dime,
quarter and penny. They did **not** confirm attaching the card to the wrist:
their reply instead said they added coins and asked us to stop asking questions.
Use the available natural wrist texture and tabletop references where possible.
Cylinder height and diameter remain unknown; the reply “yes” established card
size, not numeric cylinder dimensions. Recheck fresh scene/state after the
session gap; old images and operator attendance are historical observations.

Workspace: `/Users/fadialzuabi/hackathon/robotic-arm/geotech-robotics-hackathon`.
Always use `main`; preserve concurrent changes. Read root `AGENTS.md`,
`arm/README.md`, `arm/docs/control-and-vision.md`, `docs/maker-arm-model.md`, and
`Unreal/TheHumanTouch/SourceAssets/MakerArm/README.md`. Do not confuse the
Maker Arm with Metal Arm or YAM. No Unreal editor work occurred in this task.

## Handoff hardware state

At 17:41 Pacific, no `calibration_sweep`, `lift_probe`, `wrist_probe` or
`claw_probe` process was running. The last powered calibration attempt returned
via the SDK's verified-rest path and acknowledged release. Legacy firmware
cannot independently verify disabled mode or a motor watchdog. Do not claim
that command acknowledgment establishes either.

Final feedback report: `arm/local/opus-handoff-2026-11-07/final-arms.json`.
Three rounds on both adapters had fresh feedback on all 14 motors and zero
fault words; temperatures were 32–35 °C. Read-only diagnosis exits 1 because
some positions remain outside nominal bounds, not because motors were missing.
Approximate final SDK joint angles, degrees, in order 1–7:

| Arm | Serial | Angles |
| --- | --- | --- |
| Right | `0027002E4146570520313238` | `[2.209, -4.605, 10.319, -0.956, 10.297, 1.484, -103.776]` |
| Left | `2077378045465008` | `[10.297, -1.000, -0.363, -0.407, 3.374, -1.814, -100.347]` |

The right arm is on the **right in the overview**, but on the left in the
laptop's front view. Both grippers were visibly open. Left was not powered
during this calibration session. Gripper motor 7 was excluded from every
six-joint lift/calibration routine.

The latest proposed sweep was rejected by automatic approval review before
execution because the user had just requested wrap-up rather than another
motion run. No motor command from that proposed run executed. Do not retry it
as part of wrapping up or treat the rejection as a hardware fault.

## What is proven, and what is not

- Earlier right wrist roll: two successful 8° outbound/return routines at 3°/s.
- Earlier right elbow: successful 15° and 30° lift/return, with six-joint
  hold, SDK verified rest and release acknowledgment. See
  `arm/evidence/2026-11-07-elbow-lifts.json`.
- Earlier left wrist 2° outbound reached; return missed its completion
  tolerance. No force or automatic retry was added.
- Earlier open-only gripper tests succeeded; a complete close/open cycle and
  angle-to-gap/contact calibration are **not** established by retained evidence.
- Current camera/arm calibration collection is **incomplete**: only one
  synchronized pose observation was retained. There is no usable fit, held-out
  validation, Cartesian reaching, collision model or pickup result.
- SDK motor-coordinate forward kinematics is provisional; importing the
  upstream URDF into Unreal did not validate physical offsets or transmission.
- No Dream API call or Dream-driven physical command occurred in this task.

## Current calibration trials and retained evidence

All raw files below are ignored local files on this Mac. Compact portable
results and raw-file SHA-256 hashes are in
`arm/evidence/2026-11-07-camera-arm-calibration.json`.

1. `arm/local/natural-marker-sweep-2026-11-07/poses.json`:
   elbow lifted approximately 15°. Direct SIFT matching failed at the new view
   (`Too few unique marker matches`). Controller held; after visual inspection,
   `RETURN` completed verified rest/release. Zero pose observations.
2. `arm/local/natural-marker-sweep-retry-2026-11-07/poses.json`:
   frame-by-frame optical flow retained one 15° pose with 55 inliers. At 30°,
   the wrist reference left the bottom-right image region; spatial-coverage
   validation stopped collection. Verified rest/release after `RETURN`.
3. `arm/local/visible-marker-sweep-2026-11-07/poses.json`:
   rejected before enable by stationary velocity guard. One shoulder sample
   was about −16.62°/s while the encoder spanned one 0.02198° count. No guard
   was raised. Cause of reported velocity noise remains unestablished.
4. `arm/local/visible-marker-sweep-2026-11-07/poses-fresh-window.json`:
   new preflight passed, but initial SIFT template coverage failed before
   enable. Zero motion samples and zero observations.
5. `arm/local/calibration-current-template-2026-11-07/overview/`:
   fresh overview recording only. The proposed motor run was blocked on
   wrap-up; no `poses.json` from that run should be inferred to exist.

The revised, **not yet completed**, ten-pose plan limits elbow lift to 10–15°
so the logo remains visible, varies pan/roll by at most ±8° and shoulder by
0 to −8°, and reserves the last two poses for validation. The previous
`preflight(..., 30)` diagnostic still emits a `lift_target_deg` field with a
30° candidate; actual revised targets are in `planned_poses`. That diagnostic
label is not evidence that a 30° command ran in a rejected attempt.

## Tools and environments

Python: `arm/.venv`, 3.12.15. Base dependencies: `arm/requirements.txt`.
Optional vision packages installed and pinned in `arm/requirements-vision.txt`:
OpenCV headless 5.0.0.93 and SciPy 1.18.1. Existing NumPy is 2.5.4.
SDK pin: `0245316c05cb3e8d900f35e70c08e2e18e36731e`.
Use `bash arm/tools/arm ...` to get the macOS libusb environment setup.
Adapters use gs_usb/candlelight, 1 Mbps, MIT motor IDs 1–7.

New/modified work saved with this handoff:

- `camera_inspect` / `.swift`: 640×480 default or explicit 1920×1080;
  1–900 saved frames; atomic `capture-latest.json` per image with host uptime
  and AVFoundation PTS. Explicit output-dimension check. Real sensor exposure
  timing is unvalidated. USB HD capture worked. Laptop should use 640×480;
  it did not meet the selected HD NV12/5 fps format filter.
- `calibration_geometry.py`: pinned SDK gravity-chain raw-motor FK, initial
  SIFT texture recognition, incremental Lucas–Kanade tracking with
  forward/backward, RANSAC and spatial-coverage checks. Tracks preserve IDs
  back to the first image; no correspondence reseeding.
- `calibration_sweep.py`: guarded six-joint pose collection, image/encoder
  stable windows, held-out pose labels, return-or-hold lifecycle. It is
  experimental supervised bench code, not autonomous grasping software.
- `fit_camera_arm.py`: **unvalidated offline solver scaffold**. Jointly fits
  camera transform, focal length, radial distortion and unknown wrist-feature
  coordinates using provisional SDK FK. Requires ≥6 fit poses, ≥2 reserved
  poses, ≥8 common features; always reports `motion_ready: false`.
  Fixed principal point/equal focal assumptions and parameter observability
  require investigation. It has not run on a complete physical dataset or
  received a synthetic recovery test. Do not use its transform to move hardware.
- `test_calibration_geometry.py`: four passing tests for rigid FK/lift
  direction, bounded pose plan, invalid inputs, SIFT/blank-image rejection.
  These do not validate hardware geometry or the incremental tracker's behavior
  under all real occlusions. Base test command passes 140 tests.

Offline checks:

```sh
bash arm/tools/arm test
arm/.venv/bin/python -m pytest -q arm/tools/test_calibration_geometry.py
python3 -m py_compile arm/tools/calibration_geometry.py arm/tools/calibration_sweep.py arm/tools/fit_camera_arm.py
bash -n arm/tools/arm arm/tools/camera_inspect
python3 dashboard/build.py --check
```

## Cameras, templates and local media

Camera IDs observed this session (USB topology changes can change them):

| View | Unique ID |
| --- | --- |
| Overview | `0x11130002c7f4a60` |
| Right wrist | `0x11110002c7f4a60` |
| Left wrist | `0x11140002c7f4a60` |
| Laptop | `6C707041-05AC-0011-0002-000000000001` |

Overview/table reference image:
`arm/local/coins-and-gripper-2026-11-07/overview/camera-0x11130002c7f4a60.jpg`.
Card template: `arm/local/card-template-2026-11-07/template.png`.
Old wrist template: `arm/local/wrist-marker-2026-11-07/template.png`.
**Refreshed** wrist template: `arm/local/current-wrist-marker-2026-11-07/template.png`,
with `source.json`. It recognized 36 inliers, median error 0.146 px and 58.5%
template coverage on its source image; this is initialization evidence only.
It has not been tested in a powered multi-pose run.

The natural reference is the logo plane on the right wrist camera housing,
not the tabletop card. `CameraSequence` crops inside the detected outer housing
quad to exclude rim/connector depth discontinuities. Its tracker needs all
intermediate frames; do not delete a capture directory mid-run.

Camera helper command, read-only; use a new directory each time:

```sh
bash arm/tools/camera_inspect arm/local/NEW_SESSION/overview 900 0x11130002c7f4a60 1920x1080
```

900 frames take approximately three minutes. Start it in an independent
long-lived tool session before an authorized motion run; inspect a fresh frame
and leave sufficient recording time. `capture-ready.txt` is first-frame metadata;
`capture-latest.json` identifies the newest frame. Rebuilding/re-signing the app
can cause transient macOS camera authorization failure. The Camera permission
toggle for MakerArmCamera.app was already enabled; no privacy bypass was used.
Do not start parallel helper builds against the same app bundle.

Earlier successful 30° lift video:
`arm/local/lift-30deg-final-2026-11-07/right-elbow-lift-and-return.mp4`.

## Constraints that matter for the next session

`lift_probe.py` uses stock gains/gravity, 25 Hz, 3°/s target rate, 2° maximum
position demand ahead of measured state, 250 ms feedback freshness, 20°/s
measured-speed guard and 42 °C guard. Effort limits for motors 1–6 are
`[1.5, 5, 6, 2.5, 1, 1]` Nm. Preflight requires ≥1 second of fresh stationary
encoder samples, span ≤0.1°, drift ≤0.05°/s, gaps ≤150 ms and reported speed
≤15°/s. Do not silently relax these to get a run through.

Powered routines need `--move --operator-cutoff` and an interactive PTY.
On a powered failure, **retain the process**. It deliberately holds and waits
for inspection. `RETURN` invokes SDK verified rest/release. Never kill or
disconnect an elevated holding arm. `SUPPORTED_RELEASE` is only for an arm
physically supported by the operator. Tool session IDs from this chat are
ephemeral; no motor controller remains to resume at the handoff boundary.

Nominal gripper bounds are −96 to −2.5°, but final open readings are about
−103.8°/−100.3°. The old claw planner expects a different admissible start;
do not invoke it blindly, re-zero or extend limits. Establish measured jaw gap,
direction, transmission and low-effort contact/recovery before closing on an
object. URDF maximum total gap 0.104825 m is not a calibrated linear mapping
from SDK motor angle.

Card dimensions are confirmed, but tabletop homography and coin checks are
still provisional. An initial penny estimate was approximately 18.45 × 20.65 mm
against nominal 19.05 mm, showing scale/shape inconsistency; coin metric
validation did not pass. US Mint nominal diameters: penny 19.05 mm, dime
17.91 mm, quarter 24.26 mm; see
https://www.usmint.gov/learn/coins-and-medals/circulating-coins/coin-specifications .
The target/clutter moved during the session. Do not reach using stale pixels.

## Suggested continuation

1. Read this handoff, synchronize main without discarding changes, verify no
   other controller owns CAN, take fresh feedback and camera views.
2. Validate the new tracker and solver offline. Complete enough diverse,
   visible bounded poses to fit and independently validate alignment. Inspect
   parameter stability/conditioning and metric checks, not only pixel error.
3. Calibrate the gripper at its actual observed start with an explicit
   low-effort contact/return lifecycle. Do not reuse an unconditional release
   cleanup for elevated six-joint motion.
4. Establish table/cylinder geometry and uncertainty, reachable pregrasp and
   clearance. Only then execute a staged observed approach, grasp and short lift.
5. Record outcomes and blockers in a new dashboard update, commit focused work
   on main and push. Dream planning must pass the same physical-control boundary.

Local captures/templates are intentionally not committed; they are available
to another session on this Mac. A different machine needs those artifacts
transferred or reacquired. Never mistake the portable compact evidence for
the full calibration dataset.
