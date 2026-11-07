import math
import unittest
from maker_arm_model import frames, mounted_frames

class MakerArmModelTest(unittest.TestCase):
    def test_independent_closed_pose_geometry(self):
        f = frames([0]*6, 0)
        self.assertEqual(len(f), 10)
        self.assertAlmostEqual(f['link_003'][0][3], 0, places=6)
        self.assertAlmostEqual(f['link_003'][1][3], .0891, places=6)
        self.assertAlmostEqual(f['link_004'][0][3], .19, places=6)
        self.assertAlmostEqual(f['link_005'][0][3], .016, places=6)
        self.assertAlmostEqual(mounted_frames([0]*6, 0)['link_003'][2][3], .0891, places=6)

    def test_symmetric_jaws_across_poses(self):
        for q in ([0]*6, [.5,-1.2,1.4,.4,.2,-.7]):
            closed = frames(q, 0)
            for gap in (.02, .104825):
                f = frames(q, gap)
                a = [f['gripper_left'][i][3] for i in range(3)]
                b = [f['gripper_right'][i][3] for i in range(3)]
                self.assertAlmostEqual(math.dist(a,b), gap, places=8)
                for i in range(3):
                    self.assertAlmostEqual((a[i]+b[i])/2, closed['gripper_left'][i][3], places=8)
                    self.assertAlmostEqual(f['grasp_center'][i][3],closed['grasp_center'][i][3],places=8)

    def test_physical_station_mount_direction(self):
        f=mounted_frames([0]*6, yaw=math.pi)
        self.assertLess(f["link_004"][0][3], 0)  # Folded elbow behind base.
        self.assertGreater(f["grasp_center"][0][3], .20)  # Gripper into table.
        self.assertAlmostEqual(f["grasp_center"][1][3], 0, places=6)
        self.assertGreater(f["grasp_center"][2][3], .10)

    def test_rejects_bad_commands(self):
        for q, gap in [([0]*5,.04),([0,float('nan'),0,0,0,0],.04),([0]*6,-.01),([0]*6,.105),([0,.1,0,0,0,0],.04)]:
            with self.assertRaises(ValueError): frames(q,gap)

if __name__ == '__main__': unittest.main()
