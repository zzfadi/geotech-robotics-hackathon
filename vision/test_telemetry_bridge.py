"""vision.run --telemetry: replayed and fake-live sessions validate against geotech.telemetry/1."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from control_center import telemetry
from vision.run import main

EXAMPLE = Path(__file__).with_name("examples") / "handoff.jsonl"


def session_events(directory):
    (session,) = [p for p in Path(directory).iterdir() if p.is_dir()]
    events, _ = telemetry.read(str(session / "events.jsonl"))
    return events


def run_replay(trace, *extra):
    with tempfile.TemporaryDirectory() as tmp:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--replay", str(trace), "--telemetry", tmp] + list(extra))
        return code, session_events(tmp), out.getvalue()


def decisions(events):
    return [e for e in events if e["kind"] == "decision"]


class ReplayTelemetryTests(unittest.TestCase):
    def test_example_trace_writes_a_valid_session(self):
        code, events, stdout = run_replay(EXAMPLE)
        self.assertEqual(code, 0)
        self.assertTrue(stdout.strip())  # the JSON lines still print
        self.assertEqual([(e["seq"], p) for e in events for p in telemetry.validate(e)], [])
        self.assertEqual((events[0]["kind"], events[0]["phase"], events[0]["purpose"]), ("session", "start", "replay"))
        self.assertEqual((events[-1]["kind"], events[-1]["phase"]), ("session", "end"))
        ts = [e["ts"] for e in events]
        self.assertEqual(ts, sorted(ts))
        steps = [e.get("step") for e in decisions(events)]
        self.assertIn("offer_steady", steps)
        self.assertIn("can_released", steps)
        start = [e for e in decisions(events) if e["what"].startswith("Start the handoff")]
        self.assertEqual(len(start), 1)
        self.assertIn("Dream", start[0]["what"])
        self.assertEqual([e["mode"] for e in events if e["kind"] == "mode"], ["async"])
        received = [e["rtt_ms"] for e in events if e["kind"] == "network" and e["phase"] == "received"]
        self.assertEqual(received, [710.0, 740.0])
        cams = [e for e in events if e["kind"] == "camera"]
        self.assertTrue(cams and all(c["ok"] and c["role"] == "head" for c in cams))
        tasks = [(e["phase"], e.get("outcome")) for e in events if e["kind"] == "task"]
        self.assertEqual(tasks, [("start", None), ("step", None), ("end", "success")])

    def test_a_recorded_trace_on_monotonic_seconds_is_stamped_from_now(self):
        import time

        rows = [json.loads(line) for line in EXAMPLE.read_text().splitlines() if line.strip()]
        for row in rows:   # a --record trace carries the machine's monotonic clock, not 0-based seconds
            row["at"] += 1568151.0
            if "captured_at" in row:
                row["captured_at"] += 1568151.0
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "recorded.jsonl"
            trace.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            code, events, _ = run_replay(trace)
        self.assertEqual(code, 0)
        ts = [e["ts"] for e in events]
        self.assertEqual(ts, sorted(ts))
        self.assertLess(abs(ts[0] - time.time()), 60)
        self.assertLess(ts[-1] - ts[0], 60)          # the example spans about 3 s, not 18 days
        self.assertIn("offer_steady", [e.get("step") for e in decisions(events)])

    def test_real_target_starts_the_local_controller(self):
        _, events, _ = run_replay(EXAMPLE, "--target", "real")
        start = [e for e in decisions(events) if e["what"].startswith("Start the handoff")]
        self.assertEqual(start[0]["what"], "Start the handoff with the local controller (local_scripted)")
        self.assertIn("real arm", start[0]["why"])

    def test_withdrawal_and_a_failed_read_are_safety_decisions(self):
        hand = {"label": "hand", "conf": 0.8, "box": [0.49, 0.45, 0.59, 0.55]}
        can = {"label": "bottle", "conf": 0.6, "box": [0.47, 0.42, 0.53, 0.54]}
        rows, t = [], 0.0
        for i in range(22):
            t = i / 30
            rows.append({"at": t + 0.02, "captured_at": t, "detections": [hand, can]})
        t += 1 / 30   # the can jerks away during the reach
        rows.append({"at": t + 0.02, "captured_at": t, "detections": [
            {"label": "hand", "conf": 0.8, "box": [0.69, 0.45, 0.79, 0.55]},
            {"label": "bottle", "conf": 0.6, "box": [0.67, 0.42, 0.73, 0.54]}]})
        t += 1 / 30
        rows.append({"at": t + 0.02, "captured_at": t, "valid": False, "error": "read_failed"})
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "trace.jsonl"
            trace.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            code, events, _ = run_replay(trace)
        self.assertEqual(code, 0)
        self.assertEqual([p for e in events for p in telemetry.validate(e)], [])
        safety = [(e.get("step"), e["what"]) for e in decisions(events) if e["by"] == "safety"]
        self.assertIn(("offer_withdrawn", "The offer was withdrawn; stop"), safety)
        self.assertIn(("observation_invalid", "The detector frame is unusable; fail closed"), safety)
        self.assertTrue(any(what.startswith("Abort turn") for _, what in safety))
        bad = [e for e in events if e["kind"] == "camera" and not e["ok"]]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["checks"]["present"], "fail")
        self.assertIn(("end", "stopped"), [(e["phase"], e.get("outcome")) for e in events if e["kind"] == "task"])


class LiveTelemetryTests(unittest.TestCase):
    def test_fake_live_loop_writes_camera_health_and_decisions(self):
        import sys
        import types
        from unittest import mock

        import vision.yolo_source as yolo_source
        from vision import run
        from vision.detections import Detection, FrameDetections

        class Capture:
            def __init__(self, source):
                self.reads = 0

            def isOpened(self):
                return True

            def read(self):
                import time
                time.sleep(0.025)
                self.reads += 1
                return (self.reads <= 30, object())

            def release(self):
                pass

        class Detector:
            def __init__(self, *args, **kwargs):
                pass

            def __call__(self, frame, captured_at):
                return FrameDetections(captured_at, (Detection("hand", 0.8, (0.49, 0.45, 0.59, 0.55)),
                                                     Detection("bottle", 0.6, (0.47, 0.42, 0.53, 0.54))))

        with tempfile.TemporaryDirectory() as tmp:
            args = run.main.__globals__["argparse"].Namespace(
                detect="x.pt", pose=None, imgsz=320, video=Path("clip.mp4"), camera=None, replay=None,
                target="real", no_dream=False, local_mode="local_scripted", max_seconds=30.0,
                record=None, show=False, verbose=False, telemetry=Path(tmp), session="vision-live-test",
                camera_role="head")
            out = io.StringIO()
            with mock.patch.dict(sys.modules, {"cv2": types.SimpleNamespace(VideoCapture=Capture)}), \
                    mock.patch.object(yolo_source, "UltralyticsDetector", Detector), redirect_stdout(out):
                self.assertEqual(run.live(args), 0)
            summary = json.loads(out.getvalue().splitlines()[-1])
            self.assertEqual(summary["telemetry_session"], "vision-live-test")
            events = session_events(tmp)
        self.assertEqual([p for e in events for p in telemetry.validate(e)], [])
        self.assertEqual(events[0]["purpose"], "replay")   # a video file is a replay, not live
        cams = [e for e in events if e["kind"] == "camera"]
        self.assertTrue(cams[0]["ok"])
        self.assertFalse(cams[-1]["ok"])                    # the read that ended the clip
        self.assertIn("offer_steady", [e.get("step") for e in decisions(events)])
        self.assertEqual(events[-1]["phase"], "end")


if __name__ == "__main__":
    unittest.main()
