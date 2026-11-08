import unittest
from maker_arm_grip_hold import next_gap
class Hold(unittest.TestCase):
 def test_rate_and_command(self):
  self.assertAlmostEqual(next_gap(.05,0,.02),.0492)
  self.assertEqual(next_gap(.05,.08,.02),.05)
  self.assertEqual(next_gap(.05,.0499,.02),.0499)
  self.assertAlmostEqual(next_gap(.05,0,99),.048)
 def test_bounds(self):
  for args in ((.2,0,.01),(.05,-.01,.01),(.05,0,-1),(.05,float('nan'),.01)):
   with self.assertRaises(ValueError):next_gap(*args)
