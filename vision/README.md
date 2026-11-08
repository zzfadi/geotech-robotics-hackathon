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
