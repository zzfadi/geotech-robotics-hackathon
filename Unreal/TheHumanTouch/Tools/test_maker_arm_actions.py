import unittest
from maker_arm_actions import validate

class ActionTest(unittest.TestCase):
    def action(self,row=None):
        row=row or [0,-1,.8,0,0,0,.4]
        return {'q_l_chunk':[row[:],row[:]],'q_r_chunk':[row[:],row[:]]}
    def test_roundoff_only_is_clamped(self):
        a=self.action([0,.00004,-.00002,0,0,0,.4])
        rows=validate(a)
        self.assertEqual(rows['left'][0][1:3],[0,0])
        self.assertEqual(a['q_l_chunk'][0][1],.00004)
    def test_invalid_trajectories_rejected(self):
        for value in (float('nan'),float('inf'),100):
            a=self.action();a['q_l_chunk'][0][0]=value
            with self.assertRaises(ValueError):validate(a)
        a=self.action();a['q_r_chunk'][1][0]=1
        with self.assertRaises(ValueError):validate(a)
        a=self.action();a['q_l_chunk'][0][6]=1.01
        with self.assertRaises(ValueError):validate(a)
        a=self.action();a['q_l_chunk'].pop()
        with self.assertRaises(ValueError):validate(a)

if __name__=='__main__':unittest.main()
