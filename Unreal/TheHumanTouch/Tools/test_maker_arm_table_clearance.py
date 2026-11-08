import unittest
from maker_arm_model import mounted_frames
from maker_arm_table_clearance import check_gripper_table

class TableClearance(unittest.TestCase):
    def test_complete_pad_bounds_reject_when_tcp_itself_is_above_table(self):
        frames={jaw:[[1,0,0,0.4],[0,1,0,0],[0,0,1,.17],[0,0,0,1]]
                for jaw in ('gripper_left','gripper_right')}
        # Pad center is at base height76cm; lower face75cm is valid.
        self.assertAlmostEqual(check_gripper_table(frames,[20,0,76]),0)
        for frame in frames.values():frame[2][3] -= .01
        with self.assertRaisesRegex(ValueError,'enter tabletop'):
            check_gripper_table(frames,[20,0,76])

    def test_numerical_table_touch_tolerance(self):
        frames={jaw:[[1,0,0,.4],[0,1,0,0],[0,0,1,.169],[0,0,0,1]]
                for jaw in ('gripper_left','gripper_right')}
        self.assertAlmostEqual(check_gripper_table(frames,[20,0,76]),-.1)

    def test_fixture_pose_and_grid_translation(self):
        q=[0,-2.055434359182825,.42197567622073395,1.691617186,0,0]
        f=mounted_frames(q,.08,yaw=3.141592653589793)
        a=check_gripper_table(f,[20,-40,76])
        b=check_gripper_table(f,[840,780,76],(820,820,0))
        self.assertGreater(a,0)
        self.assertAlmostEqual(a,b)

if __name__=='__main__':unittest.main()
