"""Image-space edge cases and gate integration for hand occupancy."""

import unittest

from turn_gate.gate import Observation, TurnGate
from turn_gate.occupancy import Workspace, assess_hands


def hand_at(x, y):
    return [(x, y)] * 21


class OccupancyTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Workspace(0.3, 0.3, 0.7, 0.7)

    def test_empty_successful_result_is_clear(self):
        result = assess_hands([], self.workspace)
        self.assertTrue(result.valid)
        self.assertIs(result.hand_present, False)
        self.assertEqual(result.hand_count, 0)

    def test_full_frame_default_detects_hand_at_image_edge(self):
        result = assess_hands([hand_at(0.0, 1.0)])
        self.assertTrue(result.valid)
        self.assertIs(result.hand_present, True)

    def test_hand_inside_or_outside_workspace(self):
        for x, y, expected in ((0.5, 0.5, True), (0.1, 0.5, False), (0.5, 0.9, False)):
            with self.subTest(x=x, y=y):
                result = assess_hands([hand_at(x, y)], self.workspace)
                self.assertTrue(result.valid)
                self.assertIs(result.hand_present, expected)

    def test_boundary_touch_is_occupied(self):
        for x, y in ((0.3, 0.5), (0.7, 0.5), (0.5, 0.3), (0.5, 0.7)):
            with self.subTest(x=x, y=y):
                self.assertIs(
                    assess_hands([hand_at(x, y)], self.workspace).hand_present, True
                )

    def test_bbox_crossing_is_occupied_without_landmark_inside(self):
        hand = [(0.2, 0.5)] * 10 + [(0.8, 0.5)] * 11
        result = assess_hands([hand], self.workspace)
        self.assertIs(result.hand_present, True)
        self.assertEqual(result.hand_bounds, ((0.2, 0.5, 0.8, 0.5),))

    def test_any_hand_in_workspace_counts(self):
        hands = [hand_at(0.1, 0.1), hand_at(0.5, 0.5), hand_at(0.9, 0.9)]
        result = assess_hands(hands, self.workspace)
        self.assertIs(result.hand_present, True)
        self.assertEqual(result.hand_count, 3)
        self.assertEqual(len(result.hand_bounds), 3)

    def test_malformed_and_incomplete_hands_are_invalid(self):
        for hands in (None, {}, "", [None], [[]], [[(0.5, 0.5)] * 20], [[(0.5, 0.5)] * 22]):
            with self.subTest(hands=hands):
                result = assess_hands(hands, self.workspace)
                self.assertFalse(result.valid)
                self.assertIsNone(result.hand_present)
                self.assertEqual(result.reason, "invalid_landmarks")

    def test_invalid_points_are_not_silently_ignored(self):
        invalid_points = (
            None, {}, (0.5,), (0.5, 0.5, 0.0), ("0.5", 0.5),
            (True, 0.5), (float("nan"), 0.5), (0.5, float("inf")),
            (10 ** 1000, 0.5),
        )
        for point in invalid_points:
            with self.subTest(point_type=type(point).__name__):
                hand = hand_at(0.1, 0.1)
                hand[10] = point
                result = assess_hands([hand], self.workspace)
                self.assertFalse(result.valid)
                self.assertIsNone(result.hand_present)

    def test_out_of_frame_is_invalid_even_when_clipping_would_be_clear(self):
        for point in ((-0.01, 0.1), (1.01, 0.1), (0.1, -0.01), (0.1, 1.01)):
            with self.subTest(point=point):
                result = assess_hands([hand_at(*point)], self.workspace)
                self.assertFalse(result.valid)
                self.assertIsNone(result.hand_present)
                self.assertEqual(result.reason, "landmarks_out_of_frame")

    def test_all_hands_validated_before_occupied_result(self):
        result = assess_hands([hand_at(0.5, 0.5), []], self.workspace)
        self.assertFalse(result.valid)
        self.assertIsNone(result.hand_present)
        self.assertEqual(result.hand_bounds, ())

    def test_workspace_validation(self):
        self.assertEqual(Workspace.from_values([0, 0, 1, 1]), Workspace())
        for values in (
            None, "0,0,1,1", [0, 0, 1], [0, 0, 1, 1, 1],
            [0.5, 0, 0.5, 1], [0, 0.7, 1, 0.3],
            [-0.1, 0, 1, 1], [0, 0, 1.1, 1],
            [False, 0, 1, 1], [0, float("nan"), 1, 1], [0, 0, 10 ** 1000, 1],
        ):
            with self.subTest(value_type=type(values).__name__):
                with self.assertRaises(ValueError):
                    Workspace.from_values(values)

    def test_invalid_detection_revokes_active_turn(self):
        now = 0.0
        gate = TurnGate(clock=lambda: now)
        clear = assess_hands([], self.workspace)
        gate.observe(Observation(now, clear.hand_present, clear.valid))
        gate.request_turn()
        for now in (0.1, 0.2, 0.3, 0.4, 0.5):
            gate.observe(Observation(now, clear.hand_present, clear.valid))
        self.assertEqual(gate.snapshot().state, "ROBOT")
        now = 0.6
        invalid = assess_hands([hand_at(1.01, 0.5)], self.workspace)
        events = gate.observe(Observation(now, invalid.hand_present, invalid.valid))
        self.assertEqual(events[0].kind, "robot_turn_revoked")
        self.assertEqual(gate.snapshot().state, "HOLD")


if __name__ == "__main__":
    unittest.main()
