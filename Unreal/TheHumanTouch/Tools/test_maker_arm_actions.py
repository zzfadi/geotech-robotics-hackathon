import unittest
from maker_arm_actions import validate,retime

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

class RetimingTest(unittest.TestCase):
    def test_preserves_path_and_slows_joint_and_initial_grip(self):
        q=[0,-1,.8,0,0,0];state={'q_left':q,'q_right':q,'gap_left_m':.04,'gap_right_m':.04}
        rows=[q+[.1],[.2,-1,.8,0,0,0,.1]]
        action={'q_l_chunk':rows,'q_r_chunk':rows}
        chunks,timing=retime(action,state)
        self.assertEqual(chunks['left'][1:],rows)
        self.assertEqual(chunks['left'][0],q+[.04/.104825])
        self.assertGreater(timing['time_scale'],1)
        self.assertLessEqual(.2*timing['action_hz'],3)
        self.assertLessEqual(abs(chunks['left'][1][6]-chunks['left'][0][6])*.104825*timing['action_hz'],.2+1e-10)
        self.assertEqual(action['q_l_chunk'],rows)
    def test_bounded_duration_and_stale_start_rejected(self):
        q=[0,-1,.8,0,0,0];state={'q_left':q,'q_right':q,'gap_left_m':.04,'gap_right_m':.04}
        rows=[q+[.4],[.2,-1,.8,0,0,0,.4]]
        action={'q_l_chunk':rows,'q_r_chunk':rows}
        with self.assertRaisesRegex(ValueError,'duration'):retime(action,state,max_duration=.01)
        state['q_left']=[.5,-1,.8,0,0,0]
        with self.assertRaisesRegex(ValueError,'observed pose'):retime(action,state)
    def test_maximum_input_includes_start_sample(self):
        q=[0,-1,.8,0,0,0];state={'q_left':q,'q_right':q,'gap_left_m':.04,'gap_right_m':.04}
        rows=[q+[.04/.104825] for _ in range(200)]
        chunks,timing=retime({'q_l_chunk':rows,'q_r_chunk':rows},state)
        self.assertEqual(timing['rows'],201)

    def test_aperture_demand_saturates_at_stops_but_nonfinite_is_rejected(self):
        q=[0,-1,.8,0,0,0];state={'q_left':q,'q_right':q,'gap_left_m':.04,'gap_right_m':.04}
        action={'q_l_chunk':[q+[1.013]],'q_r_chunk':[q+[-.002]]}
        chunks,timing=retime(action,state)
        self.assertEqual(chunks['left'][-1][6],1)
        self.assertEqual(chunks['right'][-1][6],0)
        self.assertEqual(len(timing['grip_limit_corrections']),2)
        self.assertEqual(action['q_l_chunk'][0][6],1.013)
        for demand,expected in ((1.021,1),(-.031,0),(3,1),(-2,0)):
            action['q_l_chunk'][0][6]=demand
            chunks,timing=retime(action,state)
            self.assertEqual(chunks['left'][-1][6],expected)
            self.assertEqual(action['q_l_chunk'][0][6],demand)
        for bad in (float('nan'),float('inf')):
            action['q_l_chunk'][0][6]=bad
            with self.assertRaises(ValueError):retime(action,state)
    def test_does_not_repair_invalid_positions(self):
        q=[0,-1,.8,0,0,0];state={'q_left':q,'q_right':q,'gap_left_m':.04,'gap_right_m':.04}
        with self.assertRaises(ValueError):retime({'q_l_chunk':[[9]*7],'q_r_chunk':[[9]*7]},state)

if __name__=='__main__':unittest.main()
