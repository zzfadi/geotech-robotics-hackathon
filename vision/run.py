"""Run the handoff coordinator on recorded detections, a video file or a webcam.

    python3 -m vision.run --replay vision/examples/handoff.jsonl
    python3 -m vision.run --camera 0 --detect yolo26n.engine --pose yolo26n-pose.engine
    python3 -m vision.run --video clip.mp4 --detect yolo26n.pt --pose yolo26n-pose.pt

Add ``--show`` for a preview window and ``--record trace.jsonl`` to save the
session as a replay trace. Output is one JSON record per line: the moment state, turn-gate events and the
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


def _frame_record(frames: FrameDetections, at: float) -> dict:
    """The replay format, so a webcam session can be replayed and tested later."""
    record = {"at": at, "captured_at": frames.captured_at,
              "detections": [{"label": d.label, "conf": round(d.confidence, 3),
                              "box": [round(v, 4) for v in d.box]} for d in frames.detections]}
    if not frames.valid:
        record.update(valid=False, error=frames.error)
    return record


def _draw(cv2, frame, frames: FrameDetections, record: dict, zone) -> None:
    height, width = frame.shape[:2]
    colors = {"hand": (0, 0, 255), "bottle": (0, 200, 0), "cup": (0, 200, 0), "can": (0, 200, 0)}
    for d in frames.detections:
        x0, y0, x1, y1 = d.box
        cv2.rectangle(frame, (int(x0 * width), int(y0 * height)), (int(x1 * width), int(y1 * height)),
                      colors.get(d.label, (160, 160, 160)), 2)
        cv2.putText(frame, "{} {:.2f}".format(d.label, d.confidence), (int(x0 * width), int(y0 * height) - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, colors.get(d.label, (160, 160, 160)), 1)
    cv2.rectangle(frame, (int(zone.x_min * width), int(zone.y_min * height)),
                  (int(zone.x_max * width), int(zone.y_max * height)), (0, 200, 255), 2)
    moment = record.get("moment", {})
    lines = ["SOFTWARE PREVIEW - no arm attached",
             "moment {} ({})".format(moment.get("state"), moment.get("reason")),
             "gate {}  phase {}".format(record["gate"]["state"], record["phase"]),
             "latency {} ms   Q quit".format(record.get("latency_ms"))]
    for action in record["actions"]:
        lines.append("ACTION {} {}".format(action["action"], action.get("executor", "")))
    for i, line in enumerate(lines):
        cv2.putText(frame, line, (10, 22 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 2)
    cv2.imshow("Hold my beer: handoff moments", frame)


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
    recorder = args.record.open("w", encoding="utf-8") if args.record else None
    latencies, labels, moments, actions, invalid = [], {}, [], [], 0
    started = time.monotonic()
    try:
        while time.monotonic() - started < args.max_seconds:
            tick = coordinator.tick()
            if tick["actions"] or tick["gate_events"]:
                print(json.dumps(tick), flush=True)
            captured = time.monotonic()
            ok, frame = capture.read()
            if not ok:
                frames = FrameDetections(captured, (), valid=False, error="read_failed")
            else:
                frames = detector(frame, captured)
            record = coordinator.on_frame(frames)
            done = time.monotonic()
            record["latency_ms"] = round(1000 * (done - captured), 1)
            latencies.append(done - captured)
            invalid += not frames.valid
            for d in frames.detections:
                labels[d.label] = labels.get(d.label, 0) + 1
            moments += [e["kind"] for e in record["moment"]["events"]]
            actions += [a["action"] for a in record["actions"]]
            if args.verbose or record["moment"]["events"] or record["actions"] or record["gate_events"]:
                print(json.dumps(record), flush=True)
            if recorder:
                recorder.write(json.dumps(_frame_record(frames, done)) + "\n")
            if args.show and ok:
                _draw(cv2, frame, frames, record, coordinator.tracker.config.handoff)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            if not ok and args.video:
                break
    except KeyboardInterrupt:
        pass
    finally:
        capture.release()
        if recorder:
            recorder.close()
        if args.show:
            cv2.destroyAllWindows()
    ordered = sorted(latencies)
    pick = lambda q: round(1000 * ordered[min(len(ordered) - 1, int(q * len(ordered)))], 1) if ordered else None
    print(json.dumps({"type": "session_summary", "frames": len(latencies), "invalid_frames": invalid,
                      "frame_latency_ms": {"p50": pick(0.5), "p95": pick(0.95), "max": pick(1.0)},
                      "detections_by_label": labels, "moments": moments, "actions": actions,
                      "latency_basis": "read start to coordinator decision, not sensor exposure"}), flush=True)
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
    parser.add_argument("--show", action="store_true", help="preview window with boxes and state (needs a display)")
    parser.add_argument("--record", type=Path, help="write detections as a replay trace")
    parser.add_argument("--verbose", action="store_true", help="print every frame, not just changes")
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
