"""Behavioral tests using a deterministic monotonic clock and capture samples."""

import json
import unittest
from pathlib import Path

from turn_gate import Observation, TurnGate
from turn_gate.replay import replay


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TurnGateTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.gate = TurnGate(clock=self.clock)

    def observe(self, at, present=False, *, captured=None, valid=True):
        self.clock.now = at
        return self.gate.observe(Observation(
            observed_at=at if captured is None else captured,
            hand_present=present, valid=valid,
        ))

    def grant(self):
        self.observe(0.0)
        self.gate.request_turn()
        events = ()
        for at in (0.1, 0.2, 0.3, 0.4, 0.5):
            events = self.observe(at)
        self.assertEqual(self.gate.snapshot().state, "ROBOT")
        self.assertEqual(events[0].kind, "robot_turn_granted")
        return self.gate.snapshot().active_grant_id

    def test_starts_hold_and_request_before_observation_is_rejected(self):
        self.assertEqual(self.gate.snapshot().state, "HOLD")
        self.gate.request_turn()
        self.assertFalse(self.gate.snapshot().request_pending)
        for at in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5):
            self.observe(at)
        self.assertEqual(self.gate.snapshot().reason, "awaiting_turn_request")
        events = self.gate.request_turn()
        self.assertEqual(events[0].kind, "robot_turn_granted")

    def test_elapsed_time_alone_does_not_complete_debounce(self):
        self.gate = TurnGate(
            clock=self.clock, clear_for_seconds=0.2, stale_after_seconds=0.3
        )
        self.observe(0.0)
        self.gate.request_turn()
        self.clock.now = 0.2
        self.gate.tick()
        self.assertEqual(self.gate.snapshot().state, "HOLD")
        self.observe(0.21)
        self.assertEqual(self.gate.snapshot().state, "ROBOT")

    def test_positive_debounce_never_grants_on_only_one_sample(self):
        self.gate = TurnGate(clock=self.clock, clear_for_seconds=1e-13)
        self.observe(0.0)
        self.gate.request_turn()
        self.assertEqual(self.gate.snapshot().state, "HOLD")

    def test_unobserved_gap_resets_debounce_and_pending_request(self):
        self.observe(0.0)
        self.gate.request_turn()
        self.observe(0.1)
        self.observe(0.5)
        snapshot = self.gate.snapshot()
        self.assertEqual(snapshot.clear_since, 0.5)
        self.assertFalse(snapshot.request_pending)
        for at in (0.6, 0.7, 0.8, 0.9, 1.0):
            self.observe(at)
        self.assertEqual(self.gate.snapshot().state, "HOLD")
        self.gate.request_turn()
        self.assertEqual(self.gate.snapshot().state, "ROBOT")

    def test_freshness_boundary_and_cached_snapshot(self):
        grant_id = self.grant()
        self.clock.now = 0.75
        self.gate.tick()
        self.assertEqual(self.gate.snapshot().state, "ROBOT")
        self.clock.now = 0.750001
        self.assertEqual(self.gate.snapshot().state, "ROBOT")
        events = self.gate.tick()
        self.assertEqual(events[0].kind, "robot_turn_revoked")
        self.assertEqual(events[0].grant_id, grant_id)
        self.assertEqual(self.gate.snapshot().reason, "stale_observation")

    def test_hand_reentry_revokes_and_never_auto_resumes(self):
        first_grant = self.grant()
        events = self.observe(0.6, True)
        self.assertEqual(events[0].kind, "robot_turn_revoked")
        self.assertEqual(events[0].grant_id, first_grant)
        self.gate.request_turn()  # A request while occupied is rejected.
        for at in (0.7, 0.8, 0.9, 1.0, 1.1, 1.2):
            self.observe(at)
        self.assertEqual(self.gate.snapshot().state, "HOLD")
        self.assertFalse(self.gate.snapshot().request_pending)
        events = self.gate.request_turn()
        self.assertGreater(events[0].grant_id, first_grant)

    def test_invalid_occupancy_immediately_revokes(self):
        for present, valid in ((None, True), (False, False), (0, True), (False, "yes")):
            with self.subTest(present=present, valid=valid):
                self.setUp()
                self.grant()
                events = self.observe(0.6, present, valid=valid)
                self.assertEqual(events[0].kind, "robot_turn_revoked")
                self.assertEqual(self.gate.snapshot().reason, "invalid_observation")

    def test_future_old_and_duplicate_samples_revoke(self):
        for captured, reason in (
            (0.7, "future_observation"),
            (0.4, "out_of_order_observation"),
            (0.5, "duplicate_observation"),
        ):
            with self.subTest(captured=captured):
                self.setUp()
                self.grant()
                self.observe(0.6, captured=captured)
                self.assertEqual(self.gate.snapshot().state, "HOLD")
                self.assertEqual(self.gate.snapshot().reason, reason)

    def test_duplicate_frames_do_not_extend_freshness_or_clear_interval(self):
        self.observe(0.0)
        self.gate.request_turn()
        for at in (0.1, 0.2, 0.3, 0.6):
            self.observe(at, captured=0.0)
            self.assertEqual(self.gate.snapshot().state, "HOLD")
            self.assertIsNone(self.gate.snapshot().clear_since)
            self.assertFalse(self.gate.snapshot().request_pending)

    def test_new_arrival_with_stale_capture_time_does_not_restore_grant(self):
        self.grant()
        events = self.observe(1.0, captured=0.6)
        self.assertEqual(events[0].kind, "robot_turn_revoked")
        self.assertEqual(self.gate.snapshot().reason, "stale_observation")
        self.assertIsNone(self.gate.snapshot().last_observed_at)

    def test_future_sample_does_not_poison_timestamp_recovery(self):
        self.grant()
        self.observe(0.6, captured=100.0)
        self.observe(0.7)
        self.assertEqual(self.gate.snapshot().clear_since, 0.7)
        self.assertEqual(self.gate.snapshot().reason, "clear_debounce")

    def test_invalid_timestamps_revoke_without_exceptions(self):
        for captured in (float("nan"), float("inf"), "0.6", True, 10 ** 1000):
            with self.subTest(type=type(captured).__name__):
                self.setUp()
                self.grant()
                events = self.observe(0.6, captured=captured)
                self.assertEqual(events[0].kind, "robot_turn_revoked")
                self.assertEqual(self.gate.snapshot().reason, "invalid_timestamp")

    def test_completion_clears_debounce_and_rejects_preauthorization(self):
        grant_id = self.grant()
        self.assertEqual(self.gate.request_turn(), ())
        self.clock.now = 0.6
        events = self.gate.complete_turn(grant_id)
        self.assertEqual(events[0].kind, "robot_turn_completed")
        self.assertEqual(events[0].grant_id, grant_id)
        self.assertIsNone(self.gate.snapshot().clear_since)
        self.gate.request_turn()  # No fresh post-completion observation yet.
        for at in (0.7, 0.8, 0.9, 1.0, 1.1, 1.2):
            self.observe(at)
        self.assertEqual(self.gate.snapshot().state, "HOLD")
        self.gate.request_turn()
        self.assertEqual(self.gate.snapshot().active_grant_id, grant_id + 1)

    def test_late_completion_cannot_complete_a_new_grant(self):
        first_grant = self.grant()
        self.observe(0.6, True)
        for at in (0.7, 0.8, 0.9, 1.0, 1.1, 1.2):
            self.observe(at)
        self.gate.request_turn()
        second_grant = self.gate.snapshot().active_grant_id
        self.assertEqual(self.gate.complete_turn(first_grant), ())
        self.assertEqual(self.gate.snapshot().active_grant_id, second_grant)
        self.assertEqual(self.gate.snapshot().state, "ROBOT")

    def test_clock_regression_and_nonfinite_clock_revoke(self):
        for at, reason in (
            (0.4, "clock_moved_backwards"),
            (float("nan"), "invalid_clock"),
            (10 ** 1000, "invalid_clock"),
        ):
            with self.subTest(reason=reason):
                self.setUp()
                self.grant()
                self.clock.now = at
                self.gate.tick()
                self.assertEqual(self.gate.snapshot().state, "HOLD")
                self.assertEqual(self.gate.snapshot().reason, reason)

    def test_replay_has_bounded_events_and_deterministic_turn_ids(self):
        example = Path(__file__).parent / "examples" / "turn_taking.jsonl"
        records = [json.loads(line) for line in example.read_text().splitlines()]
        first = list(replay(records))
        self.assertEqual(first, list(replay(records)))
        self.assertTrue(all(len(row["events"]) <= 1 for row in first))
        turn_events = [
            event for row in first for event in row["events"]
            if event["kind"].startswith("robot_turn_")
        ]
        self.assertEqual(
            [(event["kind"], event["grant_id"]) for event in turn_events],
            [
                ("robot_turn_granted", 1), ("robot_turn_revoked", 1),
                ("robot_turn_granted", 2), ("robot_turn_completed", 2),
                ("robot_turn_granted", 3), ("robot_turn_revoked", 3),
            ],
        )
        self.assertEqual(first[-1]["snapshot"]["reason"], "stale_observation")

    def test_invalid_configuration_is_rejected(self):
        for value in (0, -1, float("inf"), float("nan"), True, 10 ** 1000):
            for field in ("clear_for_seconds", "stale_after_seconds"):
                with self.subTest(field=field, type=type(value).__name__):
                    with self.assertRaises(ValueError):
                        TurnGate(**{field: value})


if __name__ == "__main__":
    unittest.main()
