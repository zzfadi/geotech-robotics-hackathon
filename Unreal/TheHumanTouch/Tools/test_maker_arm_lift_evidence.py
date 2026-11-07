import unittest
from maker_arm_lift_evidence import update

class LiftEvidenceTests(unittest.TestCase):
    def setUp(self):self.e={'initial_z_cm':77.5,'max_z_cm':77.5};self.t={}
    def sample(self,z,t,speed=0):update(self.e,self.t,[55,-30,z],[speed,0,0],t)
    def test_sustained_height_and_low_speed(self):
        self.sample(87.5,0);self.sample(87.5,1)
        self.assertTrue(self.e['at_least_1s_slow_above_10cm']);self.assertFalse(self.e['grasp_verified'])
    def test_ballistic_height_is_not_slow_hold(self):
        self.sample(90,0,40);self.sample(95,2,30)
        self.assertEqual(self.e['max_above_10cm_seconds'],2);self.assertFalse(self.e['at_least_1s_slow_above_10cm'])
    def test_below_threshold_resets_continuity(self):
        self.sample(88,0);self.sample(88,.7);self.sample(87.49,.8);self.sample(88,.9);self.sample(88,1.4)
        self.assertAlmostEqual(self.e['max_slow_above_10cm_seconds'],.7);self.assertFalse(self.e['at_least_1s_slow_above_10cm'])
    def test_fast_interval_resets_slow_counter_only(self):
        self.sample(88,0);self.sample(88,.7);self.sample(88,.8,11);self.sample(88,.9);self.sample(88,1.4)
        self.assertEqual(self.e['max_above_10cm_seconds'],1.4);self.assertFalse(self.e['at_least_1s_slow_above_10cm'])
    def test_nonfinite_measurement_fails(self):
        with self.assertRaises(ValueError):self.sample(float('nan'),1)

if __name__=='__main__':unittest.main()
