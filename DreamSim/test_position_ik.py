import copy,json,math,unittest
from pathlib import Path
import numpy as np
from position_ik import adapt,mounted_frames

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

    def test_rejects_nonfinite_targets(self):
        action=copy.deepcopy(self.fixture['action'])
        action['per_key']['action.left_eef_xyz'][2][0]=float('nan')
        with self.assertRaises(ValueError):adapt(action,self.fixture['state'])

    def test_rejects_unreachable_target(self):
        action=copy.deepcopy(self.fixture['action'])
        action['per_key']['action.left_eef_xyz'][1]=[100,100,100]
        with self.assertRaisesRegex(ValueError,'unreachable'):adapt(action,self.fixture['state'])

if __name__=='__main__':unittest.main()
