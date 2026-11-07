"""Bounded camera-to-gate demo; software decisions only, with no robot connection."""

from __future__ import annotations

import argparse
import json
import math
import queue
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

from .gate import Observation, TurnGate
from .occupancy import Workspace, assess_hands


TIMESTAMP_BASIS = "opencv_read_start_estimate_not_sensor_exposure"


@dataclass
class FrameResult:
    frame: object
    captured_at: float
    read_finished_at: float
    inference_finished_at: float
    hands: object = None
    error: str = ""


class MediaPipeDetector:
    """Optional native dependencies load only when this detector is constructed."""

    def __init__(self, model, *, num_hands=4, detection=0.5, presence=0.5, tracking=0.5):
        import cv2
        import mediapipe as mp

        self.cv2, self.mp = cv2, mp
        self._last_ms = -1
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_hands=num_hands,
            min_hand_detection_confidence=detection,
            min_hand_presence_confidence=presence,
            min_tracking_confidence=tracking,
        )
        self.landmarker = mp.tasks.vision.HandLandmarker.create_from_options(options)

    def detect(self, frame, captured_at):
        # MediaPipe needs strictly increasing integer milliseconds. Gate timestamps
        # remain the original monotonic values, independently of this conversion.
        stamp_ms = max(self._last_ms + 1, int(captured_at * 1000))
        self._last_ms = stamp_ms
        rgb = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB)
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        result = self.landmarker.detect_for_video(image, stamp_ms)
        return [[(point.x, point.y) for point in hand] for hand in result.hand_landmarks]

    def close(self):
        self.landmarker.close()


class OpenCVSource:
    def __init__(self, camera=0, image=None):
        import cv2

        self.cv2 = cv2
        self.image = image
        self.capture = None if image else cv2.VideoCapture(camera)
        if self.capture is not None and not self.capture.isOpened():
            self.capture.release()
            raise RuntimeError("camera could not be opened")

    def read(self):
        if self.image is not None:
            frame = self.cv2.imread(str(self.image))
            return frame is not None, frame
        return self.capture.read()

    def release(self):
        if self.capture is not None:
            self.capture.release()


def _percentiles(values):
    ordered = sorted(values)
    if not ordered:
        return {"p50_ms": None, "p95_ms": None, "max_ms": None}
    return {
        "p50_ms": 1000 * ordered[max(0, math.ceil(len(ordered) * 0.5) - 1)],
        "p95_ms": 1000 * ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)],
        "max_ms": 1000 * ordered[-1],
    }


def run_camera(
    source, detector, *, workspace=Workspace(), max_seconds=60.0,
    single_frame=False, clock=time.monotonic, sleep=time.sleep,
    key_reader=lambda: "", preview=None, emit=lambda record: None,
    clear_for_seconds=0.5, stale_after_seconds=0.25, shutdown_timeout=1.0,
):
    """Run capture/inference off-thread while the main loop evaluates expiry.

    Dependencies are injectable for device-free tests. The worker cannot start a
    new read until the main loop consumes and acknowledges its one result. A
    stalled native call cannot be killed safely as a thread: join for a bounded
    interval and report worker_stopped=False on failure. The CLI must then exit;
    it must not continue as a camera-owning service. Release never races read().
    """
    gate = TurnGate(clock=clock, clear_for_seconds=clear_for_seconds,
                    stale_after_seconds=stale_after_seconds)
    stop, consumed = threading.Event(), threading.Event()
    results = queue.Queue(maxsize=1)
    cleanup_errors = []
    output_errors = []
    source_released = threading.Event()

    def cleanup_devices():
        try:
            source.release()
            source_released.set()
        except Exception as exc:
            cleanup_errors.append("camera release: " + str(exc))
        try:
            detector.close()
        except Exception as exc:
            cleanup_errors.append("detector close: " + str(exc))

    def publish(record):
        # Output errors (including a closed stdout pipe) must not bypass cleanup.
        if output_errors:
            return
        try:
            emit(record)
        except Exception as exc:
            output_errors.append("{}: {}".format(type(exc).__name__, exc))
            stop.set()
            consumed.set()

    def worker():
        try:
            while not stop.is_set():
                captured = clock()
                read_done = captured
                frame = None
                try:
                    ok, frame = source.read()
                    read_done = clock()
                    if not ok or frame is None:
                        raise RuntimeError("camera/image read failed")
                    if stop.is_set():
                        break
                    hands = detector.detect(frame, captured)
                    result = FrameResult(frame, captured, read_done, clock(), hands)
                except Exception as exc:
                    result = FrameResult(frame, captured, read_done, clock(),
                                         error="{}: {}".format(type(exc).__name__, exc))
                consumed.clear()
                while not stop.is_set():
                    try:
                        results.put(result, timeout=0.02)
                        break
                    except queue.Full:
                        pass
                while not stop.is_set() and not consumed.wait(0.02):
                    pass
                if result.error or single_frame:
                    break
        finally:
            cleanup_devices()

    thread = threading.Thread(target=worker, name="hand-camera", daemon=True)
    read_times, inference_times, ages = (deque(maxlen=10000) for _ in range(3))
    count, over_limit, over_250ms, failures, ticks = 0, 0, 0, 0, 0
    started = clock()
    last = None
    exit_code, error = 0, ""

    def publish_events(events):
        for event in events:
            publish({"type": "gate_event", **asdict(event)})

    started_thread = False
    try:
        publish({"type": "session_start", "timestamp_basis": TIMESTAMP_BASIS,
                 "mode": "offline_image" if single_frame else "camera",
                 "request_source": "manual_r_key", "max_seconds": max_seconds})
        if not stop.is_set():
            thread.start()
            started_thread = True
        while not stop.is_set() and clock() - started < max_seconds:
            publish_events(gate.tick())
            ticks += 1
            result = None
            try:
                result = results.get_nowait()
            except queue.Empty:
                pass
            if result is not None:
                try:
                    now = clock()
                    assessment = assess_hands(result.hands, workspace)
                    valid = assessment.valid and not result.error
                    publish_events(gate.observe(Observation(
                        result.captured_at, assessment.hand_present, valid=valid)))
                    age = now - result.captured_at
                    read_s = result.read_finished_at - result.captured_at
                    inference_s = result.inference_finished_at - result.read_finished_at
                    count += 1
                    failures += not valid
                    over_limit += age > stale_after_seconds
                    over_250ms += age > 0.25
                    read_times.append(read_s)
                    inference_times.append(inference_s)
                    ages.append(age)
                    last = result
                    publish({"type": "observation", "timestamp_basis": TIMESTAMP_BASIS,
                          "captured_at": result.captured_at, "processing_age_ms": age * 1000,
                          "read_ms": read_s * 1000, "inference_ms": inference_s * 1000,
                          "assessment": asdict(assessment), "error": result.error,
                          "snapshot": asdict(gate.snapshot())})
                    if result.error:
                        exit_code, error = 2, result.error
                        break
                    if single_frame:
                        break
                finally:
                    consumed.set()
            key = key_reader().lower()
            if key == "q":
                break
            if key == "r":
                publish_events(gate.request_turn())
            elif key == "c":
                grant = gate.snapshot().active_grant_id
                if grant is not None:
                    publish_events(gate.complete_turn(grant))
            if preview is not None and last is not None:
                preview(last, gate.snapshot(), workspace, clock())
            sleep(0.02)
    except KeyboardInterrupt:
        error = "interrupted"
    except Exception as exc:
        exit_code, error = 2, "{}: {}".format(type(exc).__name__, exc)
    finally:
        # End the software grant even if the device or inference has failed.
        try:
            publish_events(gate.observe(Observation(clock(), None, valid=False)))
        finally:
            stop.set()
            consumed.set()
            if started_thread:
                thread.join(timeout=shutdown_timeout)
            else:
                cleanup_devices()
    if not count and not error:
        error = "no observations before session ended"
        exit_code = 2
    if thread.is_alive() or cleanup_errors or output_errors:
        exit_code = 2
    summary = {
        "type": "session_summary", "timestamp_basis": TIMESTAMP_BASIS,
        "mode": "offline_image" if single_frame else "camera",
        "samples": count, "invalid_samples": failures, "watchdog_ticks": ticks,
        "over_freshness_limit": over_limit, "freshness_limit_ms": stale_after_seconds * 1000,
        "over_250ms": over_250ms,
        "statistics_window_samples": len(ages),
        "read_latency": _percentiles(read_times),
        "inference_latency": _percentiles(inference_times),
        "processing_age_ms": _percentiles(ages),
        "worker_stopped": not thread.is_alive(), "cleanup_errors": cleanup_errors,
        "output_errors": output_errors,
        "source_released": source_released.is_set(),
        "error": error, "exit_code": exit_code, "snapshot": asdict(gate.snapshot()),
    }
    publish(summary)
    if output_errors:
        summary["exit_code"] = 2
    return summary


def _preview(cv2, result, snapshot, workspace, now):
    frame = result.frame.copy()
    height, width = frame.shape[:2]
    assessment = assess_hands(result.hands, workspace)
    for bounds in assessment.hand_bounds:
        x_min, y_min, x_max, y_max = bounds
        color = (0, 0, 255) if workspace.intersects(bounds) else (0, 200, 0)
        cv2.rectangle(frame, (int(x_min * width), int(y_min * height)),
                      (int(x_max * width), int(y_max * height)), color, 2)
    cv2.rectangle(frame, (int(workspace.x_min * width), int(workspace.y_min * height)),
                  (int(workspace.x_max * width), int(workspace.y_max * height)), (0, 200, 255), 2)
    lines = ["SOFTWARE PREVIEW - no robot attached",
             "{}: {}".format(snapshot.state, snapshot.reason),
             "read+inference age estimate: {:.0f} ms".format(1000 * (now - result.captured_at)),
             "R request | C simulated completion | Q quit"]
    for i, line in enumerate(lines):
        cv2.putText(frame, line, (12, 25 + 25 * i), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (0, 200, 255), 2)
    cv2.imshow("Human Touch: local hand gate", frame)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    source_args = parser.add_mutually_exclusive_group()
    source_args.add_argument("--camera", type=int, default=0)
    source_args.add_argument("--image", type=Path)
    parser.add_argument("--workspace", nargs=4, type=float, default=[0, 0, 1, 1],
                        metavar=("XMIN", "YMIN", "XMAX", "YMAX"))
    parser.add_argument("--max-seconds", type=float, default=60.0)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--num-hands", type=int, default=4)
    parser.add_argument("--detection-confidence", type=float, default=0.5)
    parser.add_argument("--presence-confidence", type=float, default=0.5)
    parser.add_argument("--tracking-confidence", type=float, default=0.5)
    args = parser.parse_args(argv)
    try:
        workspace = Workspace.from_values(args.workspace)
        if not math.isfinite(args.max_seconds) or not 0 < args.max_seconds <= 3600:
            raise ValueError("max-seconds must be in (0, 3600]")
        if not 1 <= args.num_hands <= 16:
            raise ValueError("num-hands must be in [1, 16]")
        for value in (args.detection_confidence, args.presence_confidence, args.tracking_confidence):
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("confidence thresholds must be in [0, 1]")
        if not args.model.is_file():
            raise ValueError("model file does not exist")
    except ValueError as exc:
        parser.error(str(exc))
    source = detector = None
    try:
        detector = MediaPipeDetector(
            args.model, num_hands=args.num_hands, detection=args.detection_confidence,
            presence=args.presence_confidence, tracking=args.tracking_confidence)
        source = OpenCVSource(args.camera, args.image)
        import cv2
        summary = run_camera(
            source, detector, workspace=workspace, max_seconds=args.max_seconds,
            single_frame=args.image is not None,
            key_reader=(lambda: "") if args.headless or args.image else
                       (lambda: chr(cv2.waitKey(1) & 0xFF)),
            preview=None if args.headless or args.image else
                    (lambda *values: _preview(cv2, *values)),
            emit=lambda value: print(json.dumps(value, allow_nan=False), flush=True),
        )
        return summary["exit_code"]
    except Exception as exc:
        if source is None and detector is not None:
            detector.close()
        print("camera demo failed: {}".format(exc), file=sys.stderr)
        return 2
    finally:
        if not args.headless and source is not None:
            source.cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
