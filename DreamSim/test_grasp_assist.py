import unittest
try:
    import numpy as np
    from grasp_assist import action,TARGET,TCP
    from maker_arm_model import mounted_frames
    from maker_arm_actions import retime
except ImportError:
    np=None

@unittest.skipIf(np is None,'Run with numpy and scipy for the local servo tests')
class AssistTests(unittest.TestCase):
    def test_bounded_arm_only_path_lifts_contact_center(self):
        state={'q_left':[0,-1.2,.81,-.1,0,0],'q_right':[0,-1.2,.81,-.1,0,0],'gap_left_m':.09,'gap_right_m':.09}
        status={'props':{TARGET:{'position_cm':[60,-40,78.5],'initial_z_cm':78.5,'speed_cm_s':0}}}
        for phase in range(4):
            result=action(phase,state,status)
            self.assertEqual(set(result),{'q_l_chunk','q_r_chunk'})
            _,timing=retime(result,state)
            self.assertLessEqual(timing['duration_seconds'],15)
            self.assertTrue(all(row==result['q_r_chunk'][0] for row in result['q_r_chunk']))
            state['q_left']=result['q_l_chunk'][-1][:6]
            state['gap_left_m']=result['q_l_chunk'][-1][6]*.104825
        f=np.array(mounted_frames(state['q_left'],0,yaw=np.pi)['link_007'])
        pos=f[:3,3]+f[:3,:3]@TCP
        np.testing.assert_allclose(pos,[.4,0,.135],atol=.001)
        self.assertEqual(status['props'][TARGET]['position_cm'],[60,-40,78.5])
    def test_moving_or_wrong_fixture_rejected(self):
        for p in ({'position_cm':[60,-40,78.5],'initial_z_cm':78.5,'speed_cm_s':10},
                  {'position_cm':[80,-40,82],'initial_z_cm':78.5,'speed_cm_s':0}):
            with self.assertRaises(ValueError):action(0,{}, {'props':{TARGET:p}})
if __name__=='__main__':unittest.main()
