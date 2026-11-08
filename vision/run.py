"""Run the handoff coordinator on recorded detections, a video file or a webcam.

    python3 -m vision.run --replay vision/examples/handoff.jsonl
    python3 -m vision.run --camera 0 --detect yolo26n.engine --pose yolo26n-pose.engine
    python3 -m vision.run --video clip.mp4 --detect yolo26n.pt --pose yolo26n-pose.pt

Output is one JSON record per line: the moment state, turn-gate events and the
actions an arm adapter would take (start_handoff with Dream or local, abort,
retreat_with_can). Nothing here moves an arm or calls Dream. Live mode is a
single-threaded preview; the gate is ticked before every frame decision, but a
stalled camera read stalls the loop, so a real adapter needs its own watchdog
(see turn_gate/camera.py for the threaded pattern).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Iterable, Iterator

from .detections import FrameDetections
from .hybrid import ExecutorRouter, HandoffCoordinator


def replay(records: Iterable[dict], *, target: str = "sim", dream_available: bool = True,
           local_mode: str = "local_scripted") -> Iterator[dict]:
    """Records carry ``at`` (clock now) and one of: detections, tick, robot_done, dream_round_trip."""
    now = 0.0
    coordinator = HandoffCoordinator(
        router=ExecutorRouter(target=target, dream_available=dream_available, local_mode=local_mode),
        clock=lambda: now,
    )
    for record in records:
        now = float(record["at"])
        kind = record.get("action", "frame")
        if kind == "frame":
            yield coordinator.on_frame(FrameDetections.from_record(record))
        elif kind == "tick":
            yield coordinator.tick()
        elif kind == "robot_done":
            grant = coordinator.gate.snapshot().active_grant_id
            yield coordinator.robot_done(record.get("grant_id", grant))
        elif kind == "dream_round_trip":
            coordinator.router.record_round_trip(record.get("seconds"), record.get("ok", True))
        else:
            raise ValueError("unknown action: " + str(kind))


def _jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError("each line must be a JSON object")
                yield item


def live(args) -> int:
    import cv2  # optional dependency

    from .yolo_source import UltralyticsDetector

    detector = UltralyticsDetector(args.detect, args.pose, imgsz=args.imgsz)
    capture = cv2.VideoCapture(str(args.video) if args.video else args.camera)
    if not capture.isOpened():
        print("could not open the video source", file=sys.stderr)
        return 2
    coordinator = HandoffCoordinator(router=ExecutorRouter(
        target=args.target, dream_available=not args.no_dream, local_mode=args.local_mode))
    started = time.monotonic()
    try:
        while time.monotonic() - started < args.max_seconds:
            print(json.dumps(coordinator.tick()), flush=True)
            captured = time.monotonic()
            ok, frame = capture.read()
            if not ok:
                frames = FrameDetections(captured, (), valid=False, error="read_failed")
            else:
                frames = detector(frame, captured)
            record = coordinator.on_frame(frames)
            record["latency_ms"] = round(1000 * (time.monotonic() - captured), 1)
            print(json.dumps(record), flush=True)
            if not ok and args.video:
                break
    finally:
        capture.release()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--replay", type=Path, help="JSONL of recorded detections")
    source.add_argument("--camera", type=int)
    source.add_argument("--video", type=Path)
    parser.add_argument("--detect", default="yolo26n.pt", help="can detector weights or .engine")
    parser.add_argument("--pose", default="yolo26n-pose.pt", help="pose model for hands; '' to skip")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--target", choices=("sim", "real"), default="sim")
    parser.add_argument("--no-dream", action="store_true")
    parser.add_argument("--local-mode", choices=("local_scripted", "local_edge_model"), default="local_scripted")
    parser.add_argument("--max-seconds", type=float, default=60.0)
    args = parser.parse_args(argv)
    args.pose = args.pose or None
    if args.replay is None:
        return live(args)
    try:
        for output in replay(_jsonl(args.replay), target=args.target,
                             dream_available=not args.no_dream, local_mode=args.local_mode):
            print(json.dumps(output, allow_nan=False, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("replay failed: {}".format(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
