import unittest
from maker_arm_collision_audit import replay_schedule
class ReplayTiming(unittest.TestCase):
    def test_preserves_evidence_bounds(self):
        result=replay_schedule([{'call':1,'returned_action':True,'finished_unix':101.2}],
            [{'capture_requested_unix':100.,'command_seq':0},{'capture_requested_unix':101.8,'command_seq':1}],last_cycle=1)
        self.assertAlmostEqual(result[0]['earliest_seconds'],1.2)
        self.assertAlmostEqual(result[0]['latest_seconds'],1.8)
        self.assertEqual(result[0]['schedule_seconds'],result[0]['earliest_seconds'])
    def test_missing_returned_action_rejected(self):
        with self.assertRaises(ValueError):replay_schedule([], [{'capture_requested_unix':100.,'command_seq':0}],last_cycle=1)
    def test_inconsistent_timing_rejected(self):
        with self.assertRaises(ValueError):replay_schedule([{'call':1,'returned_action':True,'finished_unix':102.}],
            [{'capture_requested_unix':100.,'command_seq':0},{'capture_requested_unix':101.,'command_seq':1}],last_cycle=1)
