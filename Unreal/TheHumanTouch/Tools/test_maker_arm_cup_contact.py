import math,unittest
from maker_arm_contact_stop import local_bounds_points,contact_gap
class CupContact(unittest.TestCase):
 def points(self,tilt=0):
  c,s=math.cos(tilt),math.sin(tilt)
  return [[x,c*y-s*z,s*y+c*z-.17] for x,y,z in local_bounds_points((-.04,-.04,-.06),(.04,.04,.06),True)]
 def test_midheight_taper_not_rim_aabb(self):
  gap,names=contact_gap(0,.10,[('cup',self.points())],flat=True)
  self.assertAlmostEqual(gap,.07166666666666667-.005)
  self.assertEqual(names,['cup'])
  self.assertLess(gap,.075)
 def test_tilted_frustum_clips_finite_pad_corridor(self):
  gap,names=contact_gap(0,.10,[('cup',self.points(math.radians(10)))],flat=True)
  self.assertGreater(gap,.06);self.assertLess(gap,.08);self.assertEqual(names,['cup'])
 def test_legacy_box_unchanged(self):
  points=local_bounds_points((-.04,-.04,-.06),(.04,.04,.06))
  self.assertTrue(all(abs(p[0])==.04 and abs(p[1])==.04 for p in points))
