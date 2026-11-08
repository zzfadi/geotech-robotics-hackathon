import copy
import unittest
from pickup_verification import verify_pickup

TARGET='MakerStation_Approximate_Block_Green'
OBJECTIVE={'kind':'lift_hold','target':TARGET,'side':'left','height_cm':10.,'hold_seconds':2.}
STATUS={'sampled_unix':100.,'target':TARGET,'props':{TARGET:{'height_above_start_cm':10.5,'current_slow_above_10cm_seconds':2.2,
 'speed_cm_s':.2,'gravity_enabled':True,'physics_simulated':True,'attached_to':None}}}
REQUESTS=[{'returned_action':True,'error':None}]
MOTION=[{'max_joint_delta_rad':.2,'error':None}]

class Verification(unittest.TestCase):
    def verify(self, objective=None, status=None, requests=None, motion=None):
        return verify_pickup(objective or OBJECTIVE,TARGET,status or STATUS,
            REQUESTS if requests is None else requests,MOTION if motion is None else motion,now=101.)

    def test_elevated_object_alone_never_claims_a_grasp(self):
        result=self.verify()
        self.assertTrue(result['measured_lift_pass'])
        self.assertFalse(result['grasp_verified'])
        self.assertEqual(result['outcome'],'unverified')

    def test_exact_target_and_supported_objective(self):
        for patch in ({'target':'wrong'},{'kind':'push'},{'side':'both'},{'height_cm':12},{'hold_seconds':0}):
            result=self.verify(objective=dict(OBJECTIVE,**patch))
            self.assertFalse(result['measured_lift_pass'])
        self.assertFalse(self.verify(status=dict(STATUS,target='wrong'))['measured_lift_pass'])

    def test_stale_and_future_measurements_do_not_pass(self):
        for stamp in (None,90.,110.,float('nan')):
            self.assertFalse(self.verify(status=dict(STATUS,sampled_unix=stamp))['measured_lift_pass'])

    def test_current_hold_not_historical_peak(self):
        for patch in ({'height_above_start_cm':0,'max_z_cm':100},
                      {'current_slow_above_10cm_seconds':0,'max_slow_above_10cm_seconds':20},
                      {'speed_cm_s':15}):
            status=copy.deepcopy(STATUS);status['props'][TARGET].update(patch)
            self.assertFalse(self.verify(status=status)['measured_lift_pass'])

    def test_physics_and_no_attachment_required(self):
        for patch in ({'gravity_enabled':False},{'physics_simulated':False},{'attached_to':'robot'}):
            status=copy.deepcopy(STATUS);status['props'][TARGET].update(patch)
            self.assertFalse(self.verify(status=status)['measured_lift_pass'])

    def test_actual_returned_action_and_motion_required(self):
        self.assertFalse(self.verify(requests=[])['measured_lift_pass'])
        self.assertFalse(self.verify(requests=[{'returned_action':True,'error':'capacity'}])['measured_lift_pass'])
        self.assertFalse(self.verify(motion=[])['measured_lift_pass'])
        self.assertFalse(self.verify(motion=[{'max_joint_delta_rad':0}])['measured_lift_pass'])

    def test_nonfinite_or_missing_evidence_cannot_pass(self):
        for value in (float('nan'),float('inf'),None,True):
            status=copy.deepcopy(STATUS);status['props'][TARGET]['height_above_start_cm']=value
            self.assertFalse(self.verify(status=status)['measured_lift_pass'])
        self.assertFalse(self.verify(status={'target':TARGET,'props':{}})['measured_lift_pass'])

if __name__=='__main__':unittest.main()
