import math
import random
import unittest

import maker_arm_real_mapping as m

BASE = (12.0, -40.0, 76.0)
YAW = math.pi


def random_q(rng, limits):
    return [rng.uniform(lo, hi) for lo, hi in limits]


class MappingTest(unittest.TestCase):
    def test_wrist_yaw_is_the_only_flipped_joint(self):
        q = [0.1, -0.2, 0.3, -0.4, 0.5, -0.6]
        self.assertEqual(m.urdf_to_motor(q), [0.1, -0.2, 0.3, -0.4, -0.5, -0.6])
        self.assertEqual(m.motor_to_urdf(m.urdf_to_motor(q)), q)

    def test_drive_limits_fit_inside_model_and_motor_limits(self):
        for (lo, hi), (ulo, uhi), (mlo, mhi), s in zip(m.drive_limits(0.0), m.URDF_LIMITS,
                                                        m.MOTOR_LIMITS_DEG, m.SIGN):
            self.assertGreaterEqual(lo, ulo)
            self.assertLessEqual(hi, uhi)
            motor = sorted((math.degrees(s * lo), math.degrees(s * hi)))
            self.assertGreaterEqual(motor[0], mlo - 1e-9)
            self.assertLessEqual(motor[1], mhi + 1e-9)

    def test_gripper_endpoints(self):
        self.assertAlmostEqual(m.gap_to_gripper(0.0), 0.0)
        self.assertAlmostEqual(math.degrees(m.gap_to_gripper(m.JAW_MAX_M)), -96.0)
        self.assertAlmostEqual(m.gripper_to_gap(m.gap_to_gripper(0.05)), 0.05)
        self.assertEqual(m.gripper_to_gap(math.radians(10)), 0.0)


class KinematicsTest(unittest.TestCase):
    def test_extract_recovers_fk_joints_and_gap(self):
        rng = random.Random(4)
        for _ in range(20):
            q = random_q(rng, m.URDF_LIMITS)
            gap = rng.uniform(0, m.JAW_MAX_M)
            q2, gap2, residual = m.extract(m.fk_ue(q, gap, YAW, BASE), YAW, BASE)
            for a, b in zip(q, q2):
                self.assertAlmostEqual(a, b, places=6)
            self.assertAlmostEqual(gap, gap2, places=6)
            self.assertLess(residual, 1e-6)

    def test_extract_flags_a_dragged_gripper(self):
        poses = m.fk_ue([0.3, -1.0, 1.2, 0.2, 0.1, 0.0], 0.04, YAW, BASE)
        p, r = poses["link_007"]
        poses["link_007"] = ([p[0] + 5, p[1], p[2]], r)
        self.assertGreater(m.extract(poses, YAW, BASE)[2], 1.0)

    def test_ik_reaches_a_reachable_pose(self):
        rng = random.Random(7)
        limits = m.drive_limits()
        for _ in range(10):
            target_q = random_q(rng, limits)
            p, r = m.fk_ue(target_q, 0.04, YAW, BASE)["link_007"]
            start = m.clamp([v + rng.uniform(-0.25, 0.25) for v in target_q], limits)
            _, err_mm, err_deg = m.ik(start, 0.04, YAW, BASE, p, r, limits, iterations=40)
            self.assertLess(err_mm, 1.0)
            self.assertLess(err_deg, 1.0)

    def test_ik_never_leaves_drive_limits(self):
        limits = m.drive_limits()
        q0 = [0.0, -0.5, 0.8, 0.2, 0.0, 0.0]
        p, r = m.fk_ue(q0, 0.04, YAW, BASE)["link_007"]
        far = [p[0] + 200, p[1], p[2] + 200]  # 2 m away: unreachable
        q, err_mm, _ = m.ik(q0, 0.04, YAW, BASE, far, r, limits, iterations=40)
        for v, (lo, hi) in zip(q, limits):
            self.assertTrue(lo - 1e-12 <= v <= hi + 1e-12)
        self.assertGreater(err_mm, 100)


if __name__ == "__main__":
    unittest.main()
