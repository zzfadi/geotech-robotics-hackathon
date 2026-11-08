"""Deterministic tests: synthetic boxes and an injected clock, no camera or model."""

import json
import unittest
from pathlib import Path

from vision.detections import Detection, FrameDetections, hands_from_landmarks, hands_from_pose
from vision.hybrid import ExecutorRouter, HandoffCoordinator
from vision.moments import MomentConfig, MomentTracker, Zone
from vision.run import replay

DT = 1 / 30


def box(cx, cy, w=0.1, h=0.1):
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def hand(cx=0.54, cy=0.5):
    return Detection("hand", 0.8, box(cx, cy))


def can(cx=0.5, cy=0.48, label="bottle"):
    return Detection(label, 0.6, box(cx, cy, 0.06, 0.12))


def frame(at, *dets, valid=True):
    return FrameDetections(at, tuple(dets), valid=valid)


class DetectionTests(unittest.TestCase):
    def test_rejects_unnormalized_or_reversed_boxes(self):
        with self.assertRaises(ValueError):
            Detection("can", 0.5, (0, 0, 1.5, 1))
        with self.assertRaises(ValueError):
            Detection("can", 0.5, (0.6, 0, 0.4, 1))
        with self.assertRaises(ValueError):
            Detection("can", float("nan"), (0, 0, 1, 1))

    def test_pose_hand_sits_past_the_wrist(self):
        person = [(0.0, 0.0, 0.0)] * 17
        person[7] = (0.4, 0.4, 0.9)   # left elbow
        person[9] = (0.5, 0.4, 0.9)   # left wrist
        hands = hands_from_pose([person])
        self.assertEqual(len(hands), 1)
        cx, cy = hands[0].center
        self.assertAlmostEqual(cx, 0.535)
        self.assertAlmostEqual(cy, 0.4)

    def test_low_confidence_wrists_are_skipped(self):
        person = [(0.5, 0.5, 0.1)] * 17
        self.assertEqual(hands_from_pose([person]), ())

    def test_mediapipe_landmarks_become_a_box(self):
        landmarks = [(0.2 + i * 0.01, 0.3) for i in range(20)] + [(0.25, 0.4)]
        (only,) = hands_from_landmarks([landmarks])
        self.assertEqual(only.box, (0.2, 0.3, 0.39, 0.4))


class _List(list):
    def tolist(self):
        return list(self)


class _Boxes:
    def __init__(self, xyxyn, cls, conf):
        self.xyxyn, self.cls, self.conf = _List(xyxyn), _List(cls), _List(conf)

    def __len__(self):
        return len(self.xyxyn)


class _Keypoints:
    def __init__(self, xyn, conf):
        self.xyn, self.conf = _List(xyn), _List(conf)


class _Result:
    """Shaped like ultralytics Results for the attributes the adapter reads."""

    def __init__(self, boxes, keypoints=None):
        self.names = {0: "person", 39: "bottle", 41: "cup", 56: "chair"}
        self.boxes, self.keypoints = boxes, keypoints


class UltralyticsAdapterTests(unittest.TestCase):
    def test_keeps_can_like_classes_and_builds_hands_from_wrists(self):
        from vision.yolo_source import detections_from_result

        detect = _Result(_Boxes([[0.4, 0.4, 0.5, 0.6], [0.0, 0.0, 0.2, 0.2]], [39, 56], [0.7, 0.9]))
        self.assertEqual([d.label for d in detections_from_result(detect)], ["bottle"])
        xyn = [[(0.0, 0.0)] * 17]
        conf = [[0.0] * 17]
        xyn[0][8], conf[0][8] = (0.6, 0.5), 0.9     # right elbow
        xyn[0][10], conf[0][10] = (0.5, 0.5), 0.9   # right wrist
        pose = _Result(_Boxes([[0.3, 0.1, 0.8, 0.9]], [0], [0.9]), _Keypoints(xyn, conf))
        labels = [d.label for d in detections_from_result(pose)]
        self.assertEqual(labels, ["person", "hand"])


class MomentTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = MomentTracker()
        self.t, self.i = 0.0, 0

    def feed(self, *dets, n=1, valid=True):
        update = None
        events = []
        for _ in range(n):
            update = self.tracker.update(frame(self.t, *dets, valid=valid))
            events += [e.kind for e in update.events]
            self.i += 1
            self.t = self.i * DT
        return update, events

    def engage(self):
        update, events = self.feed(hand(), can(), n=15)
        self.assertEqual(update.state, "ENGAGED")
        self.assertIn("offer_steady", events)
        return update

    def test_empty_workspace_allows_motion_but_empty_hand_does_not(self):
        update, _ = self.feed()
        self.assertTrue(update.motion_ok)
        update, events = self.feed(hand())
        self.assertEqual((update.state, update.motion_ok), ("HAND", False))
        self.assertEqual(events, ["hand_entered"])

    def test_steady_offer_engages_after_the_steady_interval(self):
        update, events = self.feed(hand(), can(), n=12)
        self.assertEqual(update.state, "OFFERING")
        self.assertFalse(update.motion_ok)
        self.assertEqual(events, ["offer_started"])
        update, events = self.feed(hand(), can(), n=2)
        self.assertEqual((update.state, update.motion_ok), ("ENGAGED", True))

    def test_moving_can_never_engages(self):
        events = []
        for i in range(30):
            x = 0.4 + 0.01 * (i % 2)          # 0.3 frame widths per second jitter
            _, ev = self.feed(hand(x + 0.04), can(x))
            events += ev
        self.assertNotIn("offer_steady", events)
        self.assertEqual(self.tracker.reason, "can_moving")

    def test_can_held_outside_handoff_zone_is_not_an_offer(self):
        update, _ = self.feed(hand(0.94, 0.5), can(0.9, 0.5), n=20)
        self.assertEqual((update.state, update.reason), ("HAND", "hand_holding_can_outside_zone"))

    def test_second_hand_stops_motion_even_when_engaged(self):
        self.engage()
        update, events = self.feed(hand(), can(), hand(0.1, 0.1))
        self.assertEqual((update.state, update.reason, update.motion_ok), ("HAND", "multiple_hands", False))
        self.assertEqual(events, ["offer_withdrawn"])

    def test_jerked_can_withdraws_the_offer(self):
        self.engage()
        update, events = self.feed(hand(0.74), can(0.7))
        self.assertEqual((update.reason, update.motion_ok), ("can_jerked_away", False))
        self.assertEqual(events, ["offer_withdrawn"])

    def test_brief_occlusion_is_tolerated_then_fails_closed(self):
        self.engage()
        update, _ = self.feed(hand(), n=8)
        self.assertEqual((update.state, update.reason), ("ENGAGED", "can_occluded"))
        update, events = self.feed(hand(), n=2)
        self.assertEqual((update.state, update.reason), ("HAND", "can_lost"))
        self.assertEqual(events, ["offer_withdrawn"])

    def test_release_needs_the_hand_off_the_can_for_the_release_interval(self):
        self.engage()
        update, events = self.feed(hand(0.7), can(), n=8)
        self.assertEqual((update.state, update.reason), ("ENGAGED", "release_pending"))
        update, events = self.feed(hand(0.7), can(), n=2)
        self.assertEqual((update.state, update.motion_ok), ("RELEASED", True))
        self.assertEqual(events, ["can_released"])

    def test_hand_returning_to_a_released_can_stops_motion(self):
        self.engage()
        self.feed(hand(0.7), can(), n=10)
        update, events = self.feed(hand(), can())
        self.assertEqual((update.reason, update.motion_ok), ("hand_returned_to_can", False))
        self.assertEqual(events, ["hand_returned"])

    def test_invalid_and_out_of_order_frames_fail_closed(self):
        self.engage()
        update, events = self.feed(valid=False)
        self.assertEqual((update.state, update.motion_ok, update.valid), ("INVALID", None, False))
        self.assertEqual(events, ["observation_invalid"])
        update = self.tracker.update(frame(self.t - 1, hand(), can()))
        self.assertEqual(update.reason, "non_increasing_capture_time")
        self.assertFalse(update.valid)

    def test_low_confidence_and_other_labels_are_ignored(self):
        update, _ = self.feed(Detection("hand", 0.2, box(0.5, 0.5)), Detection("person", 0.9, box(0.5, 0.5, 0.4, 0.8)))
        self.assertEqual(update.state, "EMPTY")

    def test_keep_out_zone_limits_which_hands_count(self):
        tracker = MomentTracker(MomentConfig(keep_out=Zone(0.2, 0.0, 1.0, 1.0)))
        update = tracker.update(frame(0.0, hand(0.05, 0.5)))
        self.assertEqual(update.state, "EMPTY")


class RouterTests(unittest.TestCase):
    def test_real_arm_runs_local_unless_allowed(self):
        self.assertEqual(ExecutorRouter(target="real").choose().reason, "real_arm_runs_local")
        self.assertEqual(ExecutorRouter(target="real", dream_on_real=True).choose().executor, "dream")

    def test_median_of_last_five_against_850_ms(self):
        router = ExecutorRouter()
        self.assertEqual(router.choose().reason, "dream_unmeasured")
        for seconds in (0.7, 0.72, 0.9, 0.71, 0.73):
            router.record_round_trip(seconds)
        self.assertEqual(router.choose().executor, "dream")
        for seconds in (0.9, 0.95, 1.2):
            router.record_round_trip(seconds)
        plan = router.choose()
        self.assertEqual((plan.executor, plan.reason), ("local", "dream_slow"))
        self.assertEqual(plan.median_round_trip_ms, 900.0)

    def test_repeated_failures_go_local_and_a_success_recovers(self):
        router = ExecutorRouter(local_mode="local_edge_model")
        router.record_round_trip(None, ok=False)
        router.record_round_trip(0.7, ok=False)
        plan = router.choose()
        self.assertEqual((plan.mode, plan.reason), ("local_edge_model", "dream_failing"))
        router.record_round_trip(0.7)
        self.assertEqual(router.choose().executor, "dream")

    def test_last_centimetre_is_always_local(self):
        self.assertEqual(ExecutorRouter().choose().finish, "local_assist")
        self.assertEqual(ExecutorRouter(dream_available=False).choose().finish, "local_assist")


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.coordinator = HandoffCoordinator(clock=lambda: self.now)
        self.t, self.i = 0.0, 0

    def feed(self, *dets, n=1):
        actions = []
        for _ in range(n):
            self.now = self.t + 0.02
            record = self.coordinator.on_frame(frame(self.t, *dets))
            actions += record["actions"]
            self.i += 1
            self.t = self.i * DT
        return actions

    def test_steady_offer_requests_and_grants_a_dream_turn(self):
        actions = self.feed(hand(), can(), n=22)
        self.assertEqual([a["action"] for a in actions], ["start_handoff"])
        self.assertEqual(actions[0]["executor"], "dream")
        self.assertEqual(self.coordinator.gate.snapshot().state, "ROBOT")

    def test_empty_workspace_never_grants(self):
        self.assertEqual(self.feed(n=60), [])
        self.assertEqual(self.coordinator.gate.snapshot().state, "HOLD")

    def test_jerk_during_the_reach_aborts_the_turn(self):
        self.feed(hand(), can(), n=22)
        actions = self.feed(hand(0.74), can(0.7))
        self.assertEqual([a["action"] for a in actions], ["abort"])
        self.assertEqual(self.coordinator.phase, "waiting")

    def test_frames_stopping_revokes_on_tick(self):
        self.feed(hand(), can(), n=22)
        self.now = self.t + 0.5
        record = self.coordinator.tick()
        self.assertEqual([a["action"] for a in record["actions"]], ["abort"])

    def test_release_then_completion(self):
        self.feed(hand(), can(), n=22)
        grant = self.coordinator.gate.snapshot().active_grant_id
        actions = self.feed(hand(0.7), can(), n=10)
        self.assertEqual([a["action"] for a in actions], ["retreat_with_can"])
        self.feed(can(), n=3)
        record = self.coordinator.robot_done(grant)
        self.assertEqual(record["gate_events"][0]["kind"], "robot_turn_completed")
        self.assertEqual(self.coordinator.gate.snapshot().state, "HOLD")

    def test_example_trace_replays_to_one_dream_handoff(self):
        path = Path(__file__).with_name("examples") / "handoff.jsonl"
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        actions = [a for out in replay(records) for a in out["actions"]]
        self.assertEqual([a["action"] for a in actions], ["start_handoff", "retreat_with_can"])
        self.assertEqual(actions[0]["executor"], "dream")
        real = [a for out in replay(records, target="real") for a in out["actions"]]
        self.assertEqual(real[0]["executor"], "local")


class LiveLoopTests(unittest.TestCase):
    """Drive vision.run.live with a fake camera and detector, so no cv2 or model is needed."""

    def test_live_loop_records_a_replayable_trace_and_summary(self):
        import io
        import sys
        import tempfile
        import types
        from contextlib import redirect_stdout
        from unittest import mock

        import vision.yolo_source as yolo_source
        from vision import run

        class Capture:
            def __init__(self, source):
                self.reads = 0

            def isOpened(self):
                return True

            def read(self):
                import time
                time.sleep(0.025)  # about 40 fps, so the steady interval passes
                self.reads += 1
                return (self.reads <= 40, object())

            def release(self):
                pass

        class Detector:
            def __init__(self, *args, **kwargs):
                pass

            def __call__(self, frame, captured_at):
                return FrameDetections(captured_at, (hand(), can()))

        fake_cv2 = types.SimpleNamespace(VideoCapture=Capture)
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "trace.jsonl"
            args = run.main.__globals__["argparse"].Namespace(
                detect="x.pt", pose=None, imgsz=320, video=Path("clip.mp4"), camera=None,
                target="real", no_dream=False, local_mode="local_scripted", max_seconds=30.0,
                record=trace, show=False, verbose=False)
            out = io.StringIO()
            with mock.patch.dict(sys.modules, {"cv2": fake_cv2}), \
                    mock.patch.object(yolo_source, "UltralyticsDetector", Detector), redirect_stdout(out):
                self.assertEqual(run.live(args), 0)
            summary = json.loads(out.getvalue().splitlines()[-1])
            self.assertEqual(summary["frames"], 41)
            self.assertEqual(summary["invalid_frames"], 1)
            self.assertIn("offer_steady", summary["moments"])
            records = [json.loads(line) for line in trace.read_text().splitlines()]
            self.assertEqual(len(records), 41)
            replayed = [a["action"] for r in replay(records) for a in r["actions"]]
            self.assertIn("start_handoff", replayed)


if __name__ == "__main__":
    unittest.main()
