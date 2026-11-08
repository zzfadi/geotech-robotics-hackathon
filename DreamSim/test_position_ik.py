import copy,json,math,unittest
from pathlib import Path
import numpy as np
from position_ik import adapt,mounted_frames,orientation_reference

class PositionIKTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=json.loads((Path(__file__).parent/'fixtures/position_ik_regression.json').read_text())
        cls.original=copy.deepcopy(cls.fixture)
        cls.result,cls.report=adapt(cls.fixture['action'],cls.fixture['state'])

    def test_corrects_recorded_sdk_position_error_without_changing_grip(self):
        for side,key in [('left','q_l_chunk'),('right','q_r_chunk')]:
            action=self.fixture['action'];targets=np.array(action['per_key']['action.'+side+'_eef_xyz'])
            before=[];after=[]
            for i,q in enumerate(self.result[key][1:],1):
                before.append(np.linalg.norm(np.array(mounted_frames(action[key][i][:6],0,yaw=math.pi)['grasp_center'])[:3,3]-targets[i]))
                after.append(np.linalg.norm(np.array(mounted_frames(q[:6],0,yaw=math.pi)['grasp_center'])[:3,3]-targets[i]))
                self.assertEqual(q[6],action[key][i][6])
            self.assertLess(max(after),.002)
            if side=='left':self.assertGreater(max(before),.01)
            self.assertEqual(self.result[key][0][:6],self.fixture['state']['q_'+side])
        self.assertEqual(self.fixture,self.original)

    def test_reference_clamps_only_tiny_sdk_limit_roundoff(self):
        bounds=(np.array([-1.]*6),np.array([0.]*6))
        self.assertEqual(orientation_reference([6.1e-5]*6,bounds).tolist(),[0.]*6)
        with self.assertRaisesRegex(ValueError,'outside model limits'):
            orientation_reference([.001]*6,bounds)

    def test_real_rot6_response_bypasses_invalid_sdk_joint_reference(self):
        raw=json.loads((Path(__file__).parent/'fixtures/position_ik_rot6_regression.json').read_text())
        result,report=adapt(raw['action'],raw['state'])
        from compiled_fk import ArmFK
        fk=ArmFK()
        for side,key in [('left','q_l_chunk'),('right','q_r_chunk')]:
            self.assertEqual(report[side]['orientation_source'],'policy_eef_rot6')
            self.assertLess(report[side]['max_position_error_m'],.002)
            for row in result[key]:
                self.assertTrue(np.all(np.array(row[:6])>=fk.low))
                self.assertTrue(np.all(np.array(row[:6])<=fk.high))
                fk.pose(row[:6])

    def test_rejects_nonfinite_targets(self):
        action=copy.deepcopy(self.fixture['action'])
        action['per_key']['action.left_eef_xyz'][2][0]=float('nan')
        with self.assertRaises(ValueError):adapt(action,self.fixture['state'])

    def test_rejects_unreachable_target(self):
        action=copy.deepcopy(self.fixture['action'])
        action['per_key']['action.left_eef_xyz'][1]=[100,100,100]
        with self.assertRaisesRegex(ValueError,'unreachable'):adapt(action,self.fixture['state'])

if __name__=='__main__':unittest.main()
