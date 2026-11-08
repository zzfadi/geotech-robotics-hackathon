# Local synchronized demonstration capture

This is an opt-in **simulation-only, zero-Dream-API** recorder/exporter. It has not collected a live expert episode yet. No upload or training submission is implemented. Camera geometry and physics remain simulated and uncalibrated for physical transfer.

## Runtime contract

`Tools/maker_arm_demonstration_recorder.py` exposes `Recorder(directory, cameras, sample_provider, task, provenance)`. It installs no callbacks and performs no robot/object transforms. Runtime integration must supply three dedicated SceneCapture2D components/render targets under `overhead`, `wrist_l`, `wrist_r`, already following the corresponding actual cameras. Do not share targets with an asynchronous preview capture.

The authoritative controller calls `acknowledge(target_joints, action_unix)` only after both arms actually accept the target, not when a trajectory is merely planned. `target_joints` contains `q_left`, `q_right` (six URDF radians each) and `gap_left_m`, `gap_right_m` (actual actuator targets in metres). Preserve contact-limited achieved gap separately as measured state. A maintained setpoint can be acknowledged only through the actual controller update, not by refreshing a cached command timestamp.

Call `tick()` after verified physics/control sampling. Its provider returns:

```text
measured_joints: same joint/gap keys, read and verified against live links
measured_unix: actual sampling timestamp
state_verified: true only after authoritative actor verification
cup_evidence: exact geometry clearance and support evidence (see below)
```

All three captures are issued on one game-thread tick. Each request timestamp is retained; state/camera skew must be at most 20 ms. JPEG readbacks are asynchronous and the sample directory is renamed atomically only after all three complete. No images are duplicated or interpolated. Capture is 5 Hz with phase error at most 25 ms; a missed/readback-late sample rejects the episode. This is request-time synchronization, not hardware camera exposure synchronization. Actual GPU performance must be verified in a live pilot.

At the end call `finish()`, continue calling `tick()` until `closed`, then tear down the runtime. `stop()` before pending readbacks complete records an incomplete episode, which export rejects. A recorder error should terminate recording cleanly without modifying the robot or pretending the sample succeeded.

## Temporal action alignment

The exporter pairs observation/images at time **t** with the latest genuinely acknowledged command in the next sample at approximately **t+0.2 s**, requiring that acknowledgement to be strictly after t and no later than the next observation. It drops the final observation that has no future action. It does not use achieved pose as the action or manufacture a future target. Missing/stale acknowledgements reject export. The raw records retain exact measured/camera/acknowledgement timestamps and raw joints.

`DreamSim/demonstration_dataset.py` derives 32D state/action using pinned CAD FK and the existing simulator IK mounted-base/TCP convention: six joints, one normalized grip, xyz3 and rotation-6 per arm. The two arm base translations are excluded; no world coordinates enter the action vector. This matches the current virtual controller, not a validated physical SDK calibration.

## Positive episode gate

Default export requires independent named review plus a continuous five-second interval with:

- Actual cup mesh lowest point at least 10 cm above the tabletop (not just center rise).
- Tilt at most 15 degrees and speed at most 10 cm/s.
- Free physics, gravity on, no attachment.
- Complete support snapshot, bilateral jaw contact evidence and no other supports.

Overlap snapshots are geometric evidence, not solver impulses. The reviewer must examine that limitation and video before signing `grasp_and_no_external_support_verified`. Unknown support information remains unverified. Synthetic format tests are always non-positive. The V3 center-height success does not alone satisfy this stronger clearance criterion.

## Offline export and validation

```sh
PYTHONPATH=Unreal/TheHumanTouch/Tools:DreamSim DREAM_LOG=off \
uv run --python 3.11 --with 'dreamlabs-client[datasets]==1.1.3' \
  --with av --with pillow --with numpy --with imageio-ffmpeg \
  python DreamSim/demonstration_dataset.py /absolute/raw-episode /absolute/new-dataset
```

The destination must not exist. `--allow-unverified` is only for local format debugging and produces provenance with `positive_eligible: false`; it grants no upload/training permission. Encoding writes one original image per sample, without frame-generation filters, and preserves measured Parquet timestamps. A sidecar provenance file stays outside the strict dataset directory. The exporter invokes the official `dream.datasets.validate_dataset` locally and fails on errors.

Dream processes supported 5 Hz recordings into its 30 Hz training representation; that service resampling is explicitly recorded in provenance. It does not make our recorded observation rate 30 Hz. Split successful episodes by scene seed before building a training corpus. Never promote existing failed policy recordings into expert demonstrations.
