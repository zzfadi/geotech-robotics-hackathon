import math
import unittest
from yam_kinematics import frames,ue_pose,origin,model

class KinematicsTests(unittest.TestCase):
    def test_base_and_first_joint(self):
        f=frames([0]*6)
        self.assertEqual(len(f),9)
        p,_=ue_pose(f['link1'])
        self.assertAlmostEqual(p[2],6.8)
        self.assertEqual(ue_pose(f['base'])[0],[0,0,0])

    def test_gripper_coupling(self):
        opened=frames([0,2,1.4,-.8,0,0],-.04)
        closed=frames([0,2,1.4,-.8,0,0],0)
        delta=[]
        for name in ('tip_left','tip_right'):
            d=[opened[name][i][3]-closed[name][i][3] for i in range(3)]
            self.assertAlmostEqual(sum(x*x for x in d)**.5,.04)
            delta.append(d)
        for i in range(3): self.assertAlmostEqual(delta[0][i],-delta[1][i])

    def test_reject_invalid_commands(self):
        for q,f in [([0]*5,0),([float('nan')]*6,0),([10]*6,0),([0]*6,.1)]:
            with self.assertRaises(ValueError): frames(q,f)

    def test_rigid_transforms_and_quaternions(self):
        for t in frames([.3,2,1.4,-.8,.4,-.2]).values():
            for i in range(3):
                for j in range(3):
                    self.assertAlmostEqual(sum(t[k][i]*t[k][j] for k in range(3)),float(i==j))
            _,q=ue_pose(t)
            self.assertAlmostEqual(sum(v*v for v in q),1.)

if __name__=='__main__': unittest.main()
