# Dream fine-tuning readiness: Hold My Beer

Research and local audit: November 7, 2026. **No dataset uploaded and no training job created.** Fine-tuning is a possible next step after a mechanically sound cup grasp and successful demonstrations exist; it is not a substitute for correcting coordinate, camera, collision or controller errors.

## Evidence and current decision

The audit of the 12 most recent completed run summaries found seven cup runs containing 23 table episodes and **zero independently verified successful cup grasps**. These failed or unverified trials remain evaluation/debugging evidence; they must not be labeled expert positive demonstrations. See [machine-readable audit](dream-finetuning-readiness.json). This is a bounded snapshot, not a claim about all future runs.

The official SDK's local `dream.datasets.validate_dataset` rejected an existing T00 recording directory: `'action-01.json' is not inside a session folder.` That directory contains policy/debug JSON and review images/video, not a LeRobot training dataset. No network upload or Dream policy request was made by this validation.

A subsequent scripted mechanical diagnostic reported a free cup held 10.35 cm above its initial height for 5.55 seconds at 7.43 degrees tilt, without attachment. This supports mechanical feasibility; it was not Dream policy success and does not provide a synchronized expert training dataset. The bounded audit above predates that diagnostic.

## Official sources and inspected implementation

- [Dream Labs](https://dreamlabs.ai/) links its [fine-tuning platform](https://platform.dreamlabs.ai/fine-tuning).
- [Official dreamlabs-client 1.1.3 package and fine-tuning documentation](https://pypi.org/project/dreamlabs-client/1.1.3/#fine-tuning-as-a-service).
- Installed 1.1.3 implementation inspected: `dream/fine_tuning.py`, `dream/datasets.py`, `dream/facade.py`, and `shared/datasets/{validation,content_checks,video_formats,embodiments}.py`. Implementation details below describe this version and must be rechecked before a paid job.

## Dataset contract

LeRobot v2.1 layout, at the root or within session subdirectories:

```text
meta/info.json
meta/episodes.jsonl
meta/tasks.jsonl
meta/modality.json                         # optional
data/chunk-000/episode_000000.parquet
videos/chunk-000/<video_key>/episode_000000.mp4
```

Parquet must provide finite, fixed-width `action` and `observation.state`, strictly increasing `timestamp`, and consistent episode indices/lengths. Task indices, when present, must resolve to task metadata. State/action named spans must tile their vector without overlap or holes; each width is at most 128. Camera role mappings point to dataset video feature keys. `head_camera` identifies the fixed scene camera; the overhead role can provide the default. Video metadata describes height, width and channels.

Supported fixed rates are **30, 15, 10 or 5 Hz**; lower rates are resampled by the service. A usable episode covers a 48-action-frame horizon plus the following observation at 30 Hz: at least 49/25/17/9 recorded frames respectively. Maximum duration is ten hours; individual Parquet files are limited to 256 MB and metadata files to 64 MB. Supported MP4 codecs include H.264, HEVC, AV1 and VP9. These are format limits, not evidence that a short episode teaches a successful task.

Our roughly 2 fps review images and sporadic wrist observations are insufficient as synchronized demonstrations. Do not manufacture measurements by duplicating frames or treating requested actions as observed robot state. Collect timestamp-aligned overhead, left-wrist and right-wrist frames, measured state, and executed action targets at 15 or 30 Hz with explicit action units and cadence.

### Joint versus end-effector actions

Joint-space datasets need consistent named arm/gripper spans and the same units/conventions during training and inference. The project's URDF joint angles and physical SDK motor coordinates are different interfaces; an unverified mapping is not calibration.

For `use_eef=True`, the documented bimanual representation contains, for each side, `*_eef_xyz` (3), `*_eef_rot6` (6), and `*_grip` (1), in both state and action. XYZ is TCP position in metres in that arm's own base frame. Rotation-6 is the first two rotation-matrix columns in order `[R00,R10,R20,R01,R11,R21]`. Grip is normalized 0 closed / 1 open. Keep the two arm base frames explicit. Modern parts-based configurations can determine EEF behavior automatically; do not mix that path with an assumed legacy vector layout.

The inspected basic embodiment registration accepts state/action/video mappings and head-camera designation; it does **not** expose a general camera-extrinsics upload field. Preserve camera mounting/intrinsics/extrinsics, URDF provenance, coordinate conversions and simulator version in our dataset provenance, and reproduce the training camera views at inference. Structural validation can pass a semantically wrong frame convention.

## SDK workflow and cost boundaries

1. Register the reviewed embodiment using `DreamClient.add_embodiment(name, config)`.
2. Run `dream.datasets.validate_dataset(path, config)` locally; inspect the report. It needs dataset dependencies including PyArrow and PyAV, but no API key.
3. Only after data review and upload authorization, call `upload_dataset(path, name, embodiment_name)` and poll `get_dataset_status(id)` until processed. Upload registers the dataset and transfers an archive; this is not a local operation.
4. Obtain a concrete platform quote and confirm the server-selected base is the requested **standard model, not Flash**.
5. Submit the approved configuration with `finetune(dataset_name, suffix, ...)`, poll `get_finetuning_status(job_id)`, and wait for a populated `fine_tuned_model` before inference. Result IDs use `dream-1-ft:...`.

The underlying endpoints are POST/GET `/v1/fine_tuning/jobs`, GET `/v1/fine_tuning/jobs/{id}` and GET `/v1/fine_tuning/queue`. SDK queue inspection reports jobs ahead and available/total slots. `fine_tuning.training_data(model)` returns a temporary training-data download URL; it is not needed for the local audit.

Training steps accept 500–2000 in increments of 100 (default 1000); learning rate accepts 1e-6–1e-4 (default 1e-5). Batch size is service-set. Schedules include constant and cosine. `base_share=0.5` mixes general data; `0.0` trains on supplied data alone and may forget general capabilities. Resume requires compatible owned fine-tunes and matching settings and is charged for new steps.

**Important:** the SDK has `cancel(job_id)`, but its documented service behavior refuses cancellation once queued; jobs then run to completion. The subsequently inspected [official tutorial](https://platform.dreamlabs.ai/docs/fine-tuning/) lists indicative prices of $100/500 steps, $200/1000 and $400/2000; it estimates 7–8 hours for 1000 steps with two or three cameras, plus queue time. The current submission form/credit check remains authoritative; no spending cap has been approved. Account job limits remain unconfirmed. The creation interface has no explicit `base_model` argument, although job responses report a base model. Therefore explicit standard-only eligibility and an approved price ceiling remain unresolved, and no paid submission is ready for approval.

A reviewable pilot, once prerequisites are satisfied, could use 500 steps, learning rate 1e-5 and base-share 0.5; choose joint/EEF representation only after validating the recorder and action conventions. Schedule, dataset version, current quoted total price, approved spending cap and confirmed standard base must be fixed before submission. This is a proposal, not a submitted job or a guarantee of improved grasping.

## Demonstration and evaluation plan

First establish a reproducible physical simulated grasp with no object teleportation or attachment. Then collect successful expert/teleoperation demonstrations that include approach, closure, a 10 cm lift and a continuous five-second upright hold (tilt at most 15 degrees). Verify free-body gravity, stable jaw retention, no table penetration and the intended cup. Archive failures separately for evaluation and diagnosis.

Split by scene seed, cup layout and initial pose **before** dataset conversion; avoid adjacent frames from the same episode appearing in training and validation. Compare the existing standard model and fine-tune on the same held-out layouts, reporting successful holds, wrong-cup selections, drops, collisions, latency and cost. Require fresh live observations and playable synchronized recordings. SDK format validation alone does not establish task quality or grasp success.
