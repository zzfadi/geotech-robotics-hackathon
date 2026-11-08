import unittest
from maker_arm_cup_evidence import update
class CupEvidence(unittest.TestCase):
 def test_continuous_upright_hold_resets_on_tilt(self):
  e={'height_above_start_cm':11,'speed_cm_s':.1};t={}
  for i in range(52):update(e,t,10,i*.1)
  self.assertAlmostEqual(e['current_slow_upright_above_10cm_seconds'],5.1)
  update(e,t,16,5.2);self.assertEqual(e['current_slow_upright_above_10cm_seconds'],0)
  update(e,t,0,5.3);self.assertEqual(e['current_slow_upright_above_10cm_seconds'],0)
 def test_missing_samples_restart_evidence(self):
  e={'height_above_start_cm':11,'speed_cm_s':.1};t={}
  update(e,t,0,0);update(e,t,0,1)
  self.assertEqual(e['current_slow_upright_above_10cm_seconds'],0)
