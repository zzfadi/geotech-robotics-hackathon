# vision: handoff moments for the hybrid loop

The local half of the [hybrid model](../docs/hybrid-model.md): a fast detector on the Orin Nano says *when* the arm may act on a handed-over can, and a router says whether Dream or the local controller moves it. It feeds the existing [turn gate](../turn_gate/README.md) unchanged. Nothing here moves an arm or calls Dream; adapters consume the actions it prints.

## Run

From the repository root, Python 3.9+, no third-party packages for replay and tests:

```sh
python3 -m vision.run --replay vision/examples/handoff.jsonl
python3 -m vision.run --replay vision/examples/handoff.jsonl --target real
python3 -m unittest discover -s vision -t . -p 'test_*.py' -v
```

Live, with `ultralytics` and `opencv` installed in their own environment. The `.pt` weights are the default on any host (Mac, Linux, Orin); Ultralytics downloads them by name on first use. `.engine` files exist only on the Orin, after the TensorRT export below.

```sh
python3 -m vision.run --camera 0 --detect yolo26n.pt --pose yolo26n-pose.pt --target real
python3 -m vision.run --video clip.mp4 --detect yolo26n.pt --pose yolo26n-pose.pt
python3 -m vision.run --camera 0 --detect yolo26n.pt --pose yolo26n-pose.pt --device mps   # Apple GPU
```

On a Mac without touching system Python, the isolated environment used for the [Mac check](../docs/validation/vision-ultralytics-mac-2026-11-08.json):

```sh
YOLO_OFFLINE=1 UV_CACHE_DIR=vision/local/uv-cache uv run --no-project --python 3.12 \
  --with ultralytics --with opencv-python-headless python -m vision.run --video clip.mp4 \
  --detect vision/local/ultralytics/weights/yolo26n.pt --pose vision/local/ultralytics/weights/yolo26n-pose.pt --device mps
```

`opencv-python-headless` has no preview window; use `opencv-python` instead for `--show`. Ultralytics sends anonymous usage analytics when it predicts while online, unless `YOLO_OFFLINE=1` is set or `yolo settings sync=False`; leave `YOLO_OFFLINE=1` off only for the first run that downloads weights by name.

### Control center telemetry

Add `--telemetry DIR` to any run (replay, video or camera) to write a [control center](../control_center/README.md) session (`geotech.telemetry/1`) under `DIR`:

```sh
python3 -m vision.run --replay vision/examples/handoff.jsonl --telemetry vision/local/sessions
python3 -m control_center --data vision/local/sessions validate <session id>
python3 -m control_center --data vision/local/sessions serve
```

It writes camera health (only `present` is checked; colours, orientation, view and aspect are `unknown`), each moment as a decision (`offer_steady`, `can_released`; `offer_withdrawn`, `hand_returned` and `observation_invalid` as `safety`; `hand_entered`, `offer_started`, `hand_withdrawn` and `observation_recovered` as routine steps), each coordinator action (`start_handoff`, `abort`, `retreat_with_can`) as a decision and a task step, replayed `dream_round_trip` records as network events, and the router's mode against the 850 ms line. A replay session is labelled `replay`, a video file `replay`, a camera `live`. `--camera-role` (default `head`) sets the camera's role; `--session` fixes the id; use a new one each run, because an existing session is appended to.

### Dream latency probe

`vision/dream_latency.py` measures Dream round trips, feeds them to the same `ExecutorRouter` and prints the median, p95, late (over 850 ms) and failed counts and the router's verdict for that network: Dream or local.

```sh
# Mock: synthetic lognormal round trips, no key, no network, nothing billed. Labelled MOCK everywhere.
python3 -m vision.dream_latency --mock --mock-median-ms 712 --max-calls 10 --telemetry vision/local/sessions
python3 -m vision.dream_latency --mock --mock-median-ms 712,1100 --max-calls 20   # a network that degrades halfway

# Real: billed calls. Needs DREAM_API_KEY injected from the authorized store and a captured observation
# (the same state JSON and three JPEGs as DreamSim/probe.py). --max-calls (default 10, at most 50) is a hard cap.
DREAM_LOG=off uv run --no-project --python 3.12 --with dreamlabs-client==1.1.3 --with dreamlabs==1.0.37 \
  --with numpy --with pillow python -m vision.dream_latency \
  --state state.json --overhead overhead.jpg --left wrist_l.jpg --right wrist_r.jpg \
  --max-calls 10 --telemetry vision/local/sessions
```

Real mode refuses without the key or the observation, prints a cost warning, reuses `DreamSim/probe.py`'s policy config and `DreamSim/model.py`'s derived Maker Arm by import, warms the IK (not timed), then times `set_observation` + `get_action` per call, so the time includes the client's own IK solve. A reply is checked for shape and discarded; nothing moves. A call that times out (`--call-timeout`, 30 s) stops the probe. Real mode has not been run: `DREAM_API_KEY` was not available on the Mac where it was written.

### Still-image check

`vision/still_check.py` runs the adapter on still images and reports per-frame latency, labels and the tracker state they lead to. Results on FADI's Mac mini (M4), Nov 8: YOLO26n and YOLO26n-pose resolve by name in Ultralytics 8.4.207; both models together take about 48 to 53 ms per still frame on the CPU and 22 to 26 ms on MPS, excluding capture. These are Mac numbers; on the Orin, TensorRT FP16 engines take 26 to 41 ms per still frame ([Jetson hub testing](../docs/jetson-hub-testing.md)). `vision.run --video` also ran end to end with the real models on a clip made from those stills (p50 24 ms per frame on MPS after a 3 s first-frame warm-up; the recorded trace replays to the same moments; the telemetry session validates). No camera was attached. The pose-built hand boxes land on the wrist rather than over the fingers, so tune `hands_from_pose` on the real camera. Details: [vision-ultralytics-mac-2026-11-08.json](../docs/validation/vision-ultralytics-mac-2026-11-08.json).

## Orin Nano plus a webcam, no arm

The Orin runs JetPack 7.2.1 (L4T 39.2.1, Ubuntu 24.04, system Python 3.12). The C270 webcam is `/dev/video0` there. Nothing here needs the arm, the Dream key or the network once the models are on the board. The Ultralytics and TensorRT install was tested there on Nov 8, in `~/hub-tests` ([Jetson hub testing](../docs/jetson-hub-testing.md)): TensorRT FP16 engines took 26 to 41 ms per frame (5.3 to 5.7 ms inference per model), and the live C270 loop ran camera-bound at 15 fps with no hands in view. Hands and the can on the camera are still untested. Do these after SSH works ([Orin setup](../docs/handoffs/orin-nano-setup.md)).

1. **Repo and offline tests** (no extra packages):
   ```sh
   cd geotech-robotics-hackathon && git pull --ff-only origin main
   python3 -m unittest discover -s vision -t . -p 'test_*.py'
   python3 -m vision.run --replay vision/examples/handoff.jsonl --target real
   ```
   Expect `OK` and, in the replay, one `start_handoff` with `"executor": "local"` and one `retreat_with_can`.
2. **Ultralytics**, in its own environment, never the arm controller's (`/home/fadi/jetson-runtime/.venv`): follow the [Ultralytics Jetson guide](https://docs.ultralytics.com/guides/nvidia-jetson/) for JetPack 7, with the CUDA-enabled PyTorch and torchvision it lists for that release. On this Orin with JetPack 7.2.1, the plain PyPI wheel (`torch 2.15.0+cu132`) was CUDA-enabled on Nov 8; see [Jetson hub testing](../docs/jetson-hub-testing.md) for the tested install and the TensorRT FP16 export. Check with `python3 -c "import torch; print(torch.cuda.is_available())"`; it must print `True`. Record what worked in the [Jetson brief](../docs/handoffs/jetson-end-to-end.md).
3. **Check the webcam opens:** `ls /dev/video*`.
4. **First run on the `.pt` weights** (downloads `yolo26n.pt` and `yolo26n-pose.pt` once), 30 s, with a preview window on the KVM display:
   ```sh
   python3 -m vision.run --camera 0 --detect yolo26n.pt --pose yolo26n-pose.pt --device 0 --target real --show --max-seconds 30
   ```
5. **Export TensorRT engines** (Orin only, once per board), then run on them. FP16 export needs `tensorrt-cu13>=10.15,<11` (TensorRT 11 fails without NVIDIA ModelOpt) and took about 8.5 min for detect and 11 min for pose:
   ```sh
   yolo export model=yolo26n.pt format=engine quantize=16
   yolo export model=yolo26n-pose.pt format=engine quantize=16
   python3 -m vision.run --camera 0 --detect yolo26n.engine --pose yolo26n-pose.engine --target real --show \
     --record handoff-webcam.jsonl --telemetry vision/local/sessions --max-seconds 60
   ```
   `quantize=16` is the current spelling (Ultralytics 8.4.207 lists `half` as deprecated); older releases need `half=True`.
6. **Act out the handoff** in front of the webcam: an empty hand, then hold a can still in the middle of the frame (the yellow box is the handoff zone), let go of it on a table or shelf, pull your hand away. Then try the bad cases: a second hand, yanking the can away.

What to check and bring back:

- The last line, `session_summary`: `frame_latency_ms` p95 under about 100 ms on the engines, and `detections_by_label`. If `bottle` and `cup` are both near zero while the can is in view, COCO doesn't see our can and we need a small fine-tune.
- `moments` should read `hand_entered, offer_started, offer_steady, can_released`, and `actions` should hold one `start_handoff` per steady offer and an `abort` for each yank.
- Keep `handoff-webcam.jsonl` (it's a replay trace) and commit a short one under `vision/examples/` so the thresholds can be tuned against it: `python3 -m vision.run --replay handoff-webcam.jsonl`.
- Headless over SSH, leave out `--show`; the JSON lines still print.

On the real arm, `handoff/` reads `vision.run --verbose` frames as facts and starts on the operator's `g`; see [handoff/README.md](../handoff/README.md).

## Files

| File | What it does |
| --- | --- |
| `detections.py` | Normalized boxes; hand boxes from YOLO pose wrists or MediaPipe landmarks (COCO has no hand class) |
| `moments.py` | `MomentTracker`: hand and can boxes per frame to moments and a `motion_ok` verdict |
| `hybrid.py` | `ExecutorRouter` (Dream if the median of the last 5 round trips is at most 850 ms, local otherwise or on the real arm) and `HandoffCoordinator` (tracker plus turn gate, emits `start_handoff`, `abort`, `retreat_with_can`) |
| `yolo_source.py` | Optional Ultralytics adapter; a failed inference is invalid, never empty |
| `run.py` | Replay and live CLI, JSON lines out; `--telemetry` writes a control center session |
| `telemetry_bridge.py` | Maps coordinator records to `geotech.telemetry/1` events |
| `dream_latency.py` | Dream round-trip probe (mock or real, capped) feeding the router; telemetry out |
| `still_check.py` | Adapter latency, labels and tracker state on still images (needs Ultralytics) |
| `examples/handoff.jsonl` | A synthetic 3.3 s handoff: hand enters, offers, holds, lets go, withdraws |

## Replay format

One JSON object per line. `at` is the clock time the record is processed. A frame has `captured_at` and `detections` (`label`, `conf`, `box` as normalized `[x0, y0, x1, y1]`), and `valid: false` for a detector failure. Other records: `{"action": "tick"}`, `{"action": "robot_done"}`, `{"action": "dream_round_trip", "seconds": 0.71, "ok": true}`.

## Limits

Thresholds and zones are first guesses, not tuned on the real table. The example trace is synthetic. Live mode is a single-threaded preview: a stalled camera read stalls the loop, so the arm adapter needs its own watchdog and its own verified hold. This is software arbitration, not a safety system; the operator stays on the e-stop.
