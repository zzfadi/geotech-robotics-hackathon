import copy
import json
from pathlib import Path
import tempfile
import unittest
from demonstration_dataset import validate_samples, positive_eligibility, encode_state, vector, pair_future_actions

class DemonstrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.samples=[]
        v=[0.]*6+[.5]+[.1,.2,.3]+[1.,0.,0.,0.,1.,0.]
        for i in range(30):
            images={}
            for r in ('overhead','wrist_l','wrist_r'):
                name=f'{i}-{r}.jpg';(self.root/name).write_bytes(b'\xff\xd8test\xff\xd9');images[r]=name
            self.samples.append({'index':i,'state':v*2,'action':v*2,'state_verified':True,'action_applied':True,'scope':'simulation','measured_unix':100+i*.2,'action_unix':100+i*.2+.19,'camera_unix':{r:100+i*.2+.001 for r in images},'images':images,'cup_evidence':{'physics_simulated':True,'gravity_enabled':True,'attached_to':None,'support_snapshot_complete':True,'bilateral_jaw_contacts':True,'other_support_contacts':[],'lowest_point_clearance_cm':10.5,'tilt_degrees':7.,'speed_cm_s':1.}})
    def test_valid_cadence_and_review_gate(self):
        self.assertTrue(validate_samples(self.samples,self.root))
        self.assertFalse(positive_eligibility(self.samples,{}))
        self.assertTrue(positive_eligibility(self.samples,{'reviewer':'test-only','grasp_and_no_external_support_verified':True}))
    def test_missing_late_skew_reused_images_rejected(self):
        for mutation in ('late','skew','reused','unapplied'):
            s=copy.deepcopy(self.samples)
            if mutation=='late':s[5]['measured_unix']+=.1
            if mutation=='skew':s[5]['camera_unix']['wrist_l']+=.04
            if mutation=='reused':s[5]['images']['overhead']=s[4]['images']['overhead']
            if mutation=='unapplied':s[5]['action_applied']=False
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_samples(s,self.root)
    def test_center_lift_or_incomplete_support_cannot_pass(self):
        review={'reviewer':'test-only','grasp_and_no_external_support_verified':True}
        for key,value in [('lowest_point_clearance_cm',9),('support_snapshot_complete',False),('attached_to','jaw'),('other_support_contacts',['table'])]:
            s=copy.deepcopy(self.samples)
            for item in s:item['cup_evidence'][key]=value
            with self.subTest(key=key):self.assertFalse(positive_eligibility(s,review))
    def test_future_ack_pairing_rejects_identity_time_and_drops_tail(self):
        raw={'q_left':[0.]*6,'q_right':[0.]*6,'gap_left_m':.05,'gap_right_m':.08}
        samples=copy.deepcopy(self.samples)
        for i,s in enumerate(samples):
            s['measured_joints']=copy.deepcopy(raw)
            target=copy.deepcopy(raw);target['q_left'][0]=i*.001
            s['latest_action_ack']={'action_applied':True,'action_unix':s['measured_unix']-.001,'applied_target_joints':target}
        paired=pair_future_actions(samples)
        self.assertEqual(len(paired),len(samples)-1)
        self.assertEqual(paired[0]['state'][0],0)
        self.assertEqual(paired[0]['action'][0],.001)
        samples[1]['latest_action_ack']['action_unix']=samples[0]['measured_unix']
        with self.assertRaises(ValueError):pair_future_actions(samples)

    def test_fk_preserves_real_joint_and_grip_fields(self):
        raw={'q_left':[0.]*6,'q_right':[0.]*6,'gap_left_m':.05,'gap_right_m':.08}
        v=encode_state(raw);vector(v)
        self.assertEqual(len(v),32);self.assertEqual(v[:6],raw['q_left'])
        self.assertAlmostEqual(v[6],.05/.104825);self.assertAlmostEqual(v[22],.08/.104825)

if __name__=='__main__':unittest.main()
