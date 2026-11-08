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


    def test_explicit_single_arm_masks_only_inactive_policy(self):
        action=copy.deepcopy(self.fixture['action'])
        action['per_key']['action.right_eef_xyz']=[[999,999,999]]*len(action['q_r_chunk'])
        out,report=adapt(action,self.fixture['state'],active_sides=['left'])
        expected=self.fixture['state']['q_right']+[self.fixture['state']['gap_right_m']/.104825]
        self.assertTrue(report['right']['masked'])
        self.assertEqual(len(out['q_r_chunk']),len(out['q_l_chunk']))
        self.assertTrue(all(row==expected for row in out['q_r_chunk']))
        with self.assertRaises(ValueError):adapt(action,self.fixture['state'])


    def test_recorded_failures_execute_only_verified_original_target_prefix(self):
        from compiled_fk import ArmFK
        fk=ArmFK()
        data=json.loads((Path(__file__).parent/'fixtures/position_ik_prefix_regression.json').read_text())
        expected={'T06':10,'T03':5,'T01':3,'T07':2,'T00':3}
        for case in data['cases']:
            with self.subTest(table=case['table']):
                before=copy.deepcopy(case)
                output,report=adapt(case['action'],case['state'],active_sides=['left'],allow_prefix=True)
                count=expected[case['table']]
                self.assertEqual(len(output['q_l_chunk']),count)
                self.assertEqual(len(output['q_r_chunk']),count)
                self.assertTrue(report['_execution']['truncated'])
                self.assertTrue(report['_execution']['requires_fresh_replan'])
                self.assertEqual(report['_execution']['rejected_targets'][0]['row'],count)
                self.assertGreater(report['_execution']['rejected_targets'][0]['position_error_m'],.002)
                for i,row in enumerate(output['q_l_chunk'][1:],1):
                    target=case['action']['per_key']['action.left_eef_xyz'][i]
                    self.assertLessEqual(np.linalg.norm(fk.pose(row[:6])[:3,3]-target),.002)
                    self.assertEqual(row[6],case['action']['q_l_chunk'][i][6])
                self.assertEqual(case,before)

    def test_prefix_never_skips_failed_first_target(self):
        action=copy.deepcopy(self.fixture['action'])
        action['per_key']['action.left_eef_xyz'][1]=[100,100,100]
        with self.assertRaisesRegex(ValueError,'unreachable'):
            adapt(action,self.fixture['state'],active_sides=['left'],allow_prefix=True)

    def test_two_active_arms_share_earliest_feasible_prefix(self):
        action=copy.deepcopy(self.fixture['action'])
        action['per_key']['action.left_eef_xyz'][4]=[100,100,100]
        action['per_key']['action.right_eef_xyz'][2]=[100,100,100]
        output,report=adapt(action,self.fixture['state'],allow_prefix=True)
        self.assertEqual(len(output['q_l_chunk']),2)
        self.assertEqual(len(output['q_r_chunk']),2)
        self.assertEqual(report['_execution']['executed_rows'],2)

if __name__=='__main__':unittest.main()
