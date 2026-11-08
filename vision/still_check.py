"""Run the Ultralytics adapter on still images: latency, labels and what the tracker makes of them.

    uv run --no-project --python 3.12 --with ultralytics --with opencv-python-headless \
        python -m vision.still_check --detect yolo26n.pt --pose yolo26n-pose.pt \
        --device cpu --device mps --repeat 10 photo1.jpg photo2.jpg

For each image and device it times the adapter call the live loop makes
(``UltralyticsDetector.__call__``: detect, then pose, then conversion to
``FrameDetections``), after one untimed warm-up, and reports Ultralytics' own
pre/inference/post split per model. It then feeds the same detections to
``MomentTracker`` as two frames 33 ms apart to show which state the tracker
would reach. Still images only: no camera, no arm, nothing sent anywhere.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path

from .detections import FrameDetections
from .moments import MomentConfig, MomentTracker


def _tracker_view(detections, config: MomentConfig) -> dict:
    tracker = MomentTracker(config)
    first = tracker.update(FrameDetections(0.0, tuple(detections)))
    second = tracker.update(FrameDetections(1 / 30, tuple(detections)))
    used = sorted({d.label for d in detections
                   if d.label in config.hand_labels + config.can_labels and d.confidence >= config.min_confidence})
    return {"state": second.state, "reason": second.reason, "motion_ok": second.motion_ok,
            "first_frame_state": first.state, "labels_used_by_tracker": used}


def check(images, detect, pose, devices, repeat, imgsz) -> dict:
    import cv2  # optional dependency
    import torch
    import ultralytics

    from .yolo_source import UltralyticsDetector, detections_from_result

    config = MomentConfig()
    report = {
        "host": {"machine": platform.machine(), "platform": platform.platform(), "python": platform.python_version(),
                 "processor": platform.processor()},
        "versions": {"ultralytics": ultralytics.__version__, "torch": torch.__version__, "opencv": cv2.__version__},
        "mps_available": bool(torch.backends.mps.is_available()),
        "models": {"detect": str(detect), "pose": str(pose) if pose else None},
        "imgsz": imgsz, "repeat": repeat, "runs": [],
    }
    frames = {}
    for path in images:
        frame = cv2.imread(str(path))
        if frame is None:
            report["runs"].append({"image": str(path), "error": "could not read image"})
            continue
        frames[str(path)] = frame
    for device in devices:
        try:
            detector = UltralyticsDetector(str(detect), str(pose) if pose else None, imgsz=imgsz, device=device)
        except Exception as exc:
            report["runs"].append({"device": device, "error": "{}: {}".format(type(exc).__name__, exc)})
            continue
        for name, frame in frames.items():
            warm = detector(frame, 0.0)
            times, result = [], warm
            for i in range(repeat):
                started = time.perf_counter()
                result = detector(frame, float(i + 1))
                times.append((time.perf_counter() - started) * 1000.0)
            speeds = {}
            for label, model in (("detect", detector.detect), ("pose", detector.pose)):
                if model is not None:
                    raw = model(frame, **detector.kwargs)[0]
                    speeds[label] = {k: round(v, 2) for k, v in raw.speed.items()}
                    speeds[label]["raw_labels"] = sorted({str(raw.names[int(c)]) for c in raw.boxes.cls.tolist()}) \
                        if raw.boxes is not None else []
                    speeds[label]["adapter_kept"] = sorted({d.label for d in detections_from_result(raw)})
            labels = {}
            for d in result.detections:
                entry = labels.setdefault(d.label, {"count": 0, "max_conf": 0.0})
                entry["count"] += 1
                entry["max_conf"] = round(max(entry["max_conf"], d.confidence), 3)
            report["runs"].append({
                "image": name, "device": device, "size": [int(frame.shape[1]), int(frame.shape[0])],
                "valid": result.valid, "error": result.error or None, "source": result.source,
                "adapter_ms": {"median": round(statistics.median(times), 1), "min": round(min(times), 1),
                               "max": round(max(times), 1), "n": len(times)},
                "ultralytics_speed_ms": speeds,
                "labels": labels,
                "detections": [{"label": d.label, "conf": round(d.confidence, 3),
                                "box": [round(v, 4) for v in d.box]} for d in result.detections],
                "tracker": _tracker_view(result.detections, config),
            })
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--detect", default="yolo26n.pt")
    parser.add_argument("--pose", default="yolo26n-pose.pt", help="'' to skip")
    parser.add_argument("--device", action="append", help="cpu, mps, 0 (CUDA); repeatable (default cpu)")
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--out", type=Path, help="also write the JSON report here")
    args = parser.parse_args(argv)
    report = check(args.images, args.detect, args.pose or None, args.device or ["cpu"], max(1, args.repeat),
                   args.imgsz)
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if all(r.get("valid", False) and not r.get("error") for r in report["runs"]) else 1


if __name__ == "__main__":
    sys.exit(main())
