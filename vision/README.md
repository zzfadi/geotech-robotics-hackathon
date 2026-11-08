# vision: handoff moments for the hybrid loop

The local half of the [hybrid model](../docs/hybrid-model.md): a fast detector on the Orin Nano says *when* the arm may act on a handed-over can, and a router says whether Dream or the local controller moves it. It feeds the existing [turn gate](../turn_gate/README.md) unchanged. Nothing here moves an arm or calls Dream; adapters consume the actions it prints.

## Run

From the repository root, Python 3.9+, no third-party packages for replay and tests:

```sh
python3 -m vision.run --replay vision/examples/handoff.jsonl
python3 -m vision.run --replay vision/examples/handoff.jsonl --target real
python3 -m unittest discover -s vision -t . -p 'test_*.py' -v
```

Live, with `ultralytics` and `opencv` installed (on the Orin, pass TensorRT `.engine` files):

```sh
python3 -m vision.run --camera 0 --detect yolo26n.pt --pose yolo26n-pose.pt --target real
python3 -m vision.run --video clip.mp4 --detect yolo26n.pt --pose yolo26n-pose.pt
```

## Tonight: Orin Nano plus a webcam, no arm

Nothing here needs the arm, the Dream key or the network once the models are on the board. Do these after SSH works ([Orin setup](../docs/handoffs/orin-nano-setup.md)).

1. **Repo and offline tests** (no extra packages):
   ```sh
   cd geotech-robotics-hackathon && git pull --ff-only origin main
   python3 -m unittest discover -s vision -t . -p 'test_*.py'
   python3 -m vision.run --replay vision/examples/handoff.jsonl --target real
   ```
   Expect `OK` and, in the replay, one `start_handoff` with `"executor": "local"` and one `retreat_with_can`.
2. **Ultralytics on JetPack 6**, in its own environment, not the arm controller's: follow the [Ultralytics Jetson guide](https://docs.ultralytics.com/guides/nvidia-jetson/) (pip `ultralytics` plus the Jetson torch and torchvision wheels it lists for your JetPack, or its JetPack 6 Docker image). Check with `python3 -c "import torch; print(torch.cuda.is_available())"`; it must print `True`.
3. **Plug in the webcam** and check it opens: `ls /dev/video*`.
4. **First run on PyTorch weights** (downloads `yolo26n.pt` and `yolo26n-pose.pt` once), 30 s, with a preview window on the KVM display:
   ```sh
   python3 -m vision.run --camera 0 --detect yolo26n.pt --pose yolo26n-pose.pt --target real --show --max-seconds 30
   ```
5. **Export TensorRT engines** (a few minutes each, once per board), then run on them:
   ```sh
   yolo export model=yolo26n.pt format=engine quantize=16
   yolo export model=yolo26n-pose.pt format=engine quantize=16
   python3 -m vision.run --camera 0 --detect yolo26n.engine --pose yolo26n-pose.engine --target real --show --record handoff-webcam.jsonl --max-seconds 60
   ```
   Older Ultralytics releases need `half=True` instead of `quantize=16`.
6. **Act out the handoff** in front of the webcam: an empty hand, then hold a can still in the middle of the frame (the yellow box is the handoff zone), let go of it on a table or shelf, pull your hand away. Then try the bad cases: a second hand, yanking the can away.

What to check and bring back for the standup:

- The last line, `session_summary`: `frame_latency_ms` p95 under about 100 ms on the engines, and `detections_by_label`. If `bottle` and `cup` are both near zero while the can is in view, COCO doesn't see our can and we need a small fine-tune.
- `moments` should read `hand_entered, offer_started, offer_steady, can_released`, and `actions` should hold one `start_handoff` per steady offer and an `abort` for each yank.
- Keep `handoff-webcam.jsonl` (it's a replay trace) and commit a short one under `vision/examples/` so the thresholds can be tuned against it: `python3 -m vision.run --replay handoff-webcam.jsonl`.
- Headless over SSH, leave out `--show`; the JSON lines still print.

## Files

| File | What it does |
| --- | --- |
| `detections.py` | Normalized boxes; hand boxes from YOLO pose wrists or MediaPipe landmarks (COCO has no hand class) |
| `moments.py` | `MomentTracker`: hand and can boxes per frame to moments and a `motion_ok` verdict |
| `hybrid.py` | `ExecutorRouter` (Dream if the median of the last 5 round trips is at most 850 ms, local otherwise or on the real arm) and `HandoffCoordinator` (tracker plus turn gate, emits `start_handoff`, `abort`, `retreat_with_can`) |
| `yolo_source.py` | Optional Ultralytics adapter; a failed inference is invalid, never empty |
| `run.py` | Replay and live CLI, JSON lines out |
| `examples/handoff.jsonl` | A synthetic 3.3 s handoff: hand enters, offers, holds, lets go, withdraws |

## Replay format

One JSON object per line. `at` is the clock time the record is processed. A frame has `captured_at` and `detections` (`label`, `conf`, `box` as normalized `[x0, y0, x1, y1]`), and `valid: false` for a detector failure. Other records: `{"action": "tick"}`, `{"action": "robot_done"}`, `{"action": "dream_round_trip", "seconds": 0.71, "ok": true}`.

## Limits

Thresholds and zones are first guesses, not tuned on the real table. The example trace is synthetic. Live mode is a single-threaded preview: a stalled camera read stalls the loop, so the arm adapter needs its own watchdog and its own verified hold. This is software arbitration, not a safety system; the operator stays on the e-stop.
