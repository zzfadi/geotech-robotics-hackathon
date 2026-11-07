import unittest
from maker_arm_trajectory import sample

class TrajectoryTest(unittest.TestCase):
    def test_continuous_midpoint(self):self.assertEqual(sample([[0,1],[2,3]],.025,20),([1,2],0))
    def test_end_clamps(self):self.assertEqual(sample([[0],[2]],10,20),([2],1))
    def test_no_negative_extrapolation(self):self.assertEqual(sample([[0],[2]],-1,20),([0],0))
    def test_velocity_preserved(self):
        rows=[[0],[.1],[.2]]
        samples=[sample(rows,i*.01,20)[0][0] for i in range(11)]
        self.assertTrue(all(abs(b-a)<=.02+1e-12 for a,b in zip(samples,samples[1:])))
