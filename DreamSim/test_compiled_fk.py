import sys, unittest
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'Unreal/TheHumanTouch/Tools'))
from compiled_fk import ArmFK, decode_rot6
from maker_arm_model import mounted_frames

class CompiledFKTests(unittest.TestCase):
    def test_matches_official_frames_over_random_joint_limits(self):
        fk=ArmFK();rng=np.random.default_rng(415)
        for q in [np.zeros(6)]+list(rng.uniform(fk.low,fk.high,size=(100,6))):
            np.testing.assert_allclose(fk.pose(q),mounted_frames(q,0,yaw=np.pi)['grasp_center'],atol=2e-15)
        with self.assertRaises(ValueError):fk.pose(fk.high+.0001)

    def test_dream_tcp_rot6_roundtrip(self):
        fk=ArmFK()
        for q in [np.zeros(6),np.array([.2,-1,1,.3,.2,-.5])]:
            cad=fk.pose(q)[:3,:3]
            tcp=cad@fk.tool_axes
            encoded=np.r_[tcp[:,0],tcp[:,1]]
            np.testing.assert_allclose(decode_rot6(encoded)@fk.tool_axes.T,cad,atol=1e-15)
        with self.assertRaises(ValueError):decode_rot6(np.zeros(6))
        with self.assertRaises(ValueError):decode_rot6([1,0,0,2,0,0])

if __name__=='__main__':unittest.main()
