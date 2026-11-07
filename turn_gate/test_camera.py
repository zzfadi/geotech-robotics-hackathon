"""Camera runtime tests with no OpenCV, MediaPipe, camera or network."""

import threading
import time
import unittest

from turn_gate.camera import TIMESTAMP_BASIS, run_camera


HAND = [[(0.4, 0.4)] * 21]


class Clock:
    def __init__(self):
        self.now = 0.0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.now

    def sleep(self, seconds):
        with self.lock:
            self.now += seconds
        time.sleep(0.001)  # yield to the capture thread; virtual time drives expiry


class Source:
    def __init__(self, read=None):
        self.reader = read or (lambda: (True, object()))
        self.released = False
        self.released_event = threading.Event()
        self.reads = 0

    def read(self):
        if self.released:
            raise AssertionError("capture attempted after release")
        self.reads += 1
        return self.reader()

    def release(self):
        self.released = True
        self.released_event.set()


class Detector:
    def __init__(self, detect=None):
        self.infer = detect or (lambda frame, stamp: [])
        self.closed = False

    def detect(self, frame, stamp):
        return self.infer(frame, stamp)

    def close(self):
        self.closed = True


class CameraTests(unittest.TestCase):
    def run_fake(self, source, detector, **kwargs):
        clock = Clock()
        records = []
        custom_emit = kwargs.pop("emit", lambda value: None)

        def emit(record):
            records.append(record)
            custom_emit(record)

        result = run_camera(source, detector, clock=clock, sleep=clock.sleep,
                            max_seconds=3, clear_for_seconds=0.04,
                            stale_after_seconds=0.1, emit=emit, **kwargs)
        return result, records

    def test_one_image_is_not_repeated_and_never_grants(self):
        source, detector = Source(), Detector(lambda frame, stamp: HAND)
        summary, records = self.run_fake(source, detector, single_frame=True)
        observations = [r for r in records if r["type"] == "observation"]
        self.assertEqual(source.reads, 1)
        self.assertEqual(len(observations), 1)
        self.assertTrue(observations[0]["assessment"]["hand_present"])
        self.assertEqual(observations[0]["snapshot"]["reason"], "hand_present")
        self.assertEqual(summary["snapshot"]["state"], "HOLD")
        self.assertEqual(summary["timestamp_basis"], TIMESTAMP_BASIS)
        self.assertEqual(summary["exit_code"], 0)
        self.assertTrue(source.released and detector.closed)

    def _test_stall(self, stall_in_read):
        granted, blocked, unblock, revoked = (threading.Event() for _ in range(4))

        def stall():
            if granted.is_set() and not blocked.is_set():
                blocked.set()
                if not unblock.wait(2):
                    raise AssertionError("watchdog did not revoke blocked work")

        def read():
            if stall_in_read:
                stall()
            return True, object()

        def detect(frame, stamp):
            if not stall_in_read:
                stall()
            return []

        def emit(record):
            if record.get("kind") == "robot_turn_granted":
                granted.set()
            elif record.get("kind") == "robot_turn_revoked" and blocked.is_set():
                self.assertEqual(record["reason"], "stale_observation")
                revoked.set()
                unblock.set()

        source, detector = Source(read), Detector(detect)
        summary, records = self.run_fake(
            source, detector, emit=emit,
            key_reader=lambda: "q" if revoked.is_set() else "r")
        self.assertTrue(granted.is_set() and blocked.is_set() and revoked.is_set())
        self.assertGreater(summary["watchdog_ticks"], summary["samples"])
        self.assertEqual(summary["snapshot"]["state"], "HOLD")
        self.assertTrue(summary["worker_stopped"] and summary["source_released"])
        self.assertTrue(source.released and detector.closed)

    def test_watchdog_runs_while_inference_stalls(self):
        self._test_stall(stall_in_read=False)

    def test_watchdog_runs_while_capture_stalls(self):
        self._test_stall(stall_in_read=True)

    def test_hand_reentry_is_delivered_then_completion_needs_new_grant(self):
        grants, revoked, completed = [], [], []
        interrupted = threading.Event()
        return_hand = threading.Event()

        def detect(frame, stamp):
            return HAND if return_hand.is_set() and not interrupted.is_set() else []

        def emit(record):
            kind = record.get("kind")
            if kind == "robot_turn_granted":
                grants.append(record["grant_id"])
                if len(grants) == 1:
                    return_hand.set()
            elif kind == "robot_turn_revoked" and record["reason"] == "hand_present":
                revoked.append(record["grant_id"])
                interrupted.set()
            elif kind == "robot_turn_completed":
                completed.append(record["grant_id"])

        def key():
            if completed:
                return "q"
            return "c" if len(grants) == 2 else "r"

        source, detector = Source(), Detector(detect)
        summary, records = self.run_fake(source, detector, key_reader=key, emit=emit)
        self.assertEqual(grants, [1, 2])
        self.assertEqual(revoked, [1])
        self.assertEqual(completed, [2])
        self.assertEqual(summary["snapshot"]["state"], "HOLD")
        self.assertTrue(source.released and detector.closed)

    def test_read_failure_holds_and_cleans_up(self):
        source, detector = Source(lambda: (False, None)), Detector()
        summary, records = self.run_fake(source, detector)
        self.assertEqual(summary["exit_code"], 2)
        self.assertEqual(summary["invalid_samples"], 1)
        self.assertEqual(summary["snapshot"]["state"], "HOLD")
        self.assertTrue(source.released and detector.closed)

    def test_inference_failure_is_invalid_not_clear(self):
        def fail(frame, stamp):
            raise RuntimeError("detector unavailable")

        source, detector = Source(), Detector(fail)
        summary, records = self.run_fake(source, detector)
        observation = next(r for r in records if r["type"] == "observation")
        self.assertFalse(observation["assessment"]["valid"])
        self.assertIsNone(observation["assessment"]["hand_present"])
        self.assertEqual(summary["exit_code"], 2)
        self.assertTrue(source.released and detector.closed)

    def test_malformed_landmarks_never_grant(self):
        source, detector = Source(), Detector(lambda frame, stamp: [[(float("nan"), 0)] * 21])
        summary, records = self.run_fake(source, detector, single_frame=True)
        self.assertEqual(summary["invalid_samples"], 1)
        self.assertEqual(summary["snapshot"]["state"], "HOLD")
        self.assertFalse(any(r.get("kind") == "robot_turn_granted" for r in records))

    def test_slow_consumer_cannot_overwrite_occupied_result(self):
        seen = []
        count = [0]

        def detect(frame, stamp):
            count[0] += 1
            return HAND if count[0] == 2 else []

        def emit(record):
            if record["type"] == "observation":
                seen.append(record["assessment"]["hand_present"])
                time.sleep(0.01)

        source, detector = Source(), Detector(detect)
        summary, _ = self.run_fake(source, detector, emit=emit,
                                  key_reader=lambda: "q" if len(seen) >= 3 else "")
        self.assertEqual(seen[:3], [False, True, False])
        self.assertTrue(summary["worker_stopped"])

    def test_output_failure_always_closes_devices(self):
        for fail_on in ("session_start", "observation", "gate_event", "session_summary"):
            with self.subTest(fail_on=fail_on):
                def emit(record):
                    if record["type"] == fail_on:
                        raise BrokenPipeError("closed output")

                source, detector = Source(), Detector()
                summary, _ = self.run_fake(source, detector, emit=emit, single_frame=True)
                self.assertEqual(summary["exit_code"], 2)
                self.assertTrue(summary["output_errors"])
                self.assertEqual(summary["snapshot"]["state"], "HOLD")
                self.assertTrue(source.released and detector.closed)
                self.assertTrue(summary["worker_stopped"])

    def test_quit_without_input_does_not_report_success(self):
        blocked, release = threading.Event(), threading.Event()

        def detect(frame, stamp):
            blocked.set()
            release.wait(1)
            return []

        def key():
            release.set()
            return "q"

        source, detector = Source(), Detector(detect)
        summary, _ = self.run_fake(source, detector, key_reader=key)
        self.assertEqual(summary["samples"], 0)
        self.assertEqual(summary["exit_code"], 2)
        self.assertIn("no observations", summary["error"])
        self.assertTrue(source.released and detector.closed)

    def test_shutdown_emission_failure_still_cleans_up(self):
        source, detector = Source(), Detector()
        observed = [False]

        def emit(record):
            if record["type"] == "observation":
                observed[0] = True
            elif record["type"] == "gate_event" and observed[0]:
                raise BrokenPipeError("failed only during final hold")

        summary, _ = self.run_fake(source, detector, emit=emit, single_frame=True)
        self.assertEqual(summary["exit_code"], 2)
        self.assertTrue(source.released and detector.closed)
        self.assertTrue(summary["worker_stopped"])
        self.assertEqual(summary["snapshot"]["state"], "HOLD")

    def test_native_stall_reports_incomplete_shutdown_without_release_race(self):
        release = threading.Event()
        entered = threading.Event()

        def read():
            entered.set()
            release.wait(2)
            return True, object()

        source, detector = Source(read), Detector()
        try:
            summary, _ = self.run_fake(source, detector, shutdown_timeout=0.01,
                                      key_reader=lambda: "q" if entered.is_set() else "")
            self.assertEqual(summary["exit_code"], 2)
            self.assertFalse(summary["worker_stopped"])
            self.assertFalse(summary["source_released"])
            self.assertFalse(source.released)  # never release concurrently with read
            self.assertEqual(summary["snapshot"]["state"], "HOLD")
        finally:
            release.set()
            self.assertTrue(source.released_event.wait(1))


if __name__ == "__main__":
    unittest.main()
