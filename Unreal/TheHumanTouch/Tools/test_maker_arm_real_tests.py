import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import maker_arm_real_mapping as mapping  # noqa: E402
import maker_arm_real_tests as rt  # noqa: E402

YAWS = {'left': math.pi, 'right': math.pi}
BASES = {'left': (2480., -40., 76.), 'right': (2480., 40., 76.)}
GAPS = {'left': .1, 'right': .1}
REST = {'left': [math.radians(v) for v in (-6, -3.2, 1.4, -5, 4.5, -4.2)],
        'right': [math.radians(v) for v in (4.5, -4.8, 9.5, -1.3, -1.2, 1.3)]}
LIMITS = mapping.drive_limits()


def peak_speed(motion, hz=30.):
    worst, t, prev = 0., 0., rt.sample(motion, 0.)
    while t < motion['duration_s']:
        t += 1/hz
        cur = rt.sample(motion, t)
        worst = max(worst, max(abs(a-b) for s in rt.SIDES for a, b in zip(cur[s], prev[s]))*hz)
        prev = cur
    return worst


class TestMotions(unittest.TestCase):
    def test_idle_reaches_the_shared_rest_inside_limits(self):
        motion = rt.plan('idle', REST, GAPS, YAWS, BASES, LIMITS)
        end = rt.sample(motion, motion['duration_s'])
        for side in rt.SIDES:
            self.assertEqual([round(math.degrees(v), 6) for v in end[side]], list(rt.IDLE_DEG))
            self.assertTrue(all(lo <= v <= hi for v, (lo, hi) in zip(end[side], LIMITS)))
        self.assertEqual(rt.sample(motion, 0.), REST)

    def test_move_up_from_rest_lifts_both_grippers_straight_up(self):
        motion = rt.plan('move_up', REST, GAPS, YAWS, BASES, LIMITS)
        end = rt.sample(motion, motion['duration_s'])
        for side in rt.SIDES:
            start = mapping.fk_ue(mapping.clamp(REST[side], LIMITS), .1, YAWS[side], BASES[side])['link_007'][0]
            top = mapping.fk_ue(end[side], .1, YAWS[side], BASES[side])['link_007'][0]
            self.assertAlmostEqual(top[2]-start[2], rt.MOVE_UP_CM, delta=.3)
            self.assertLess(math.dist(top[:2], start[:2]), .3)
        self.assertLessEqual(peak_speed(motion), rt.MAX_SPEED_RAD_S*1.05)

    def test_unknown_action_and_unreachable_moves_are_rejected(self):
        with self.assertRaises(ValueError):
            rt.plan('wave', REST, GAPS, YAWS, BASES, LIMITS)
        q = {s: list(REST[s]) for s in rt.SIDES}
        for _ in range(12):  # keep pressing "move up" until the arm runs out of reach
            try:
                motion = rt.plan('move_up', q, GAPS, YAWS, BASES, LIMITS)
            except ValueError as error:
                self.assertIn('cannot move', str(error))
                break
            q = rt.sample(motion, motion['duration_s'])
        else:
            self.fail('move_up never reached the edge of the workspace')


if __name__ == '__main__':
    unittest.main()
