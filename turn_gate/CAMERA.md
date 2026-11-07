# Hand detector preview

This is the next step after the gate replay: local MediaPipe inference produces hand landmarks, a normalized workspace rectangle turns those into occupancy, and the existing gate decides whether to grant a software turn. No arm or policy is connected.

## Setup

The optional camera dependencies were checked with Python 3.12 on macOS arm64. The gate and unit tests still run with the standard library on Python 3.9.

```sh
python3.12 -m venv .venv-turn-gate
.venv-turn-gate/bin/python -m pip install -r turn_gate/requirements-camera.txt
mkdir -p turn_gate/local
curl --fail --location --output turn_gate/local/hand_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
shasum -a 256 turn_gate/local/hand_landmarker.task
```

Expected model SHA-256: `fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1`.

The model URL comes from Google's [official Python sample](https://github.com/google-ai-edge/mediapipe-samples/blob/main/examples/hand_landmarker/python/hand_landmarker.ipynb). The implementation uses the [Hand Landmarker Tasks API](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker?tab=Python). Model files, local images and the virtual environment are ignored by Git. Inference runs locally; the preview does not upload frames or record camera images.

## Start the preview

```sh
.venv-turn-gate/bin/python -m turn_gate.camera \
  --model turn_gate/local/hand_landmarker.task --camera 0 --max-seconds 60
```

Focus the preview window:

- **R** requests a robot turn after a fresh clear observation. Permission waits for the clear debounce; an interrupted turn needs another request.
- **C** simulates completion of the current turn. This is a manual preview control, not evidence that an arm completed a move.
- **Q** quits. The preview also stops at the configured time limit.

Key press is the first demo's request source. Voice can call the same gate API later; it must not automatically request/resume a turn just because a hand disappears.

The full image is the default workspace. For an overhead view, use `--workspace X_MIN Y_MIN X_MAX Y_MAX` with normalized coordinates in `[0,1]`, for example `--workspace 0.1 0.1 0.9 0.9`. Check the displayed rectangle against the actual reachable area. The image is not mirrored. Any detected hand bounding box intersecting or touching the rectangle is occupied. Missing points, nonfinite coordinates or landmarks outside the image make the result invalid. The default maximum is four hands; configure `--num-hands` for the people in view.

`--headless` disables the window and keyboard, allowing bounded detection/logging without granting turns. The process emits JSON events and a timing summary; normal camera frames are not printed or saved. Camera or inference failure ends in `HOLD` with a nonzero exit status.

## Offline model check

Use a local image with `--image PATH` instead of `--camera`. It performs one inference and finishes in `HOLD`; a static image is never looped or retimestamped into a live grant.

```sh
curl --fail --location --output turn_gate/local/woman_hands.jpg \
  https://storage.googleapis.com/mediapipe-tasks/hand_landmarker/woman_hands.jpg
.venv-turn-gate/bin/python -m turn_gate.camera \
  --model turn_gate/local/hand_landmarker.task \
  --image turn_gate/local/woman_hands.jpg --headless
python3 -m unittest discover -s turn_gate -p 'test_*.py' -v
```

The sample image is referenced by Google's notebook above. Its SHA-256 is `70cbeb38e198c9862202e0979c21a99b40ca980d3e7b250176c85b1636a40f12`. Downloading this sample is optional; it is not bundled into the repository.

## What timing proves

The watchdog runs independently of capture/inference and checks the gate while those operations are blocked. A bounded handoff between worker and main loop preserves every inferred occupancy result; an occupied result cannot be silently overwritten by a later clear one.

OpenCV does not provide a verified camera exposure timestamp here. The preview conservatively uses the local time just **before** `read()` as an estimate, and reports `timestamp_basis` accordingly. Read duration, inference duration and processing age can reveal an overrun of the gate's 0.25-second freshness limit. They do not measure sensor exposure-to-stop latency or exclude hidden driver buffering. Do not increase that limit merely to make a slow pipeline grant turns.

On the station, verify actual capture timing/source health, calibrate the workspace, test lighting and occlusion, and measure hand-entry-to-revocation latency before integrating actuators. A successful empty detection result means the model found no hands; it does not prove the area is physically clear. Read the [robot integration boundary](../docs/robot-stack-contract.md) before connecting commands.

## Verification on November 7, 2026

The real MediaPipe 1.2.2 / OpenCV 5.0.0 runtime loaded the pinned model on this Mac with Python 3.12.15. The official sample produced two detected hands, a valid occupied observation and `HOLD`; the worker stopped and released the source. The final image-mode CLI check measured 11.75 ms read, 33.01 ms inference and 60.15 ms processing age. These are a single offline smoke check, not station performance measurements. All 41 standard-library tests pass, including 24 occupancy/camera tests independently reviewed and run.

The live-camera CLI returned exit status 2 after OpenCV reported macOS camera access denied. No system privacy settings were changed. Live hand tracking, workspace calibration and camera timing remain unverified; run the preview on an authorized station camera to complete those checks.
