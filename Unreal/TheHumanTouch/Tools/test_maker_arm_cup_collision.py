import math
import unittest
from maker_arm_cup_collision import cup_hulls,contains_point

class HollowCupCollision(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.hulls=cup_hulls()
    def inside(self,p):return any(contains_point(h,p) for h in self.hulls)
    def test_exact_dimensions_and_count(self):
        self.assertEqual(len(self.hulls),33)
        pts=[p for h in self.hulls for p in h['vertices_cm']]
        self.assertEqual(min(p[2] for p in pts),-6)
        self.assertEqual(max(p[2] for p in pts),6)
        self.assertAlmostEqual(max(math.hypot(*p[:2]) for p in pts),4)
    def test_finger_can_enter_open_cavity(self):
        # All corners of a3x1cm finger cross-section remain clear from above
        # rim down to0.01cm above the modeled inside floor.
        for z in [7,6,5,3,0,-3,-5.69]:
            for x in [-1.5,0,1.5]:
                for y in [-.5,0,.5]:self.assertFalse(self.inside([x,y,z]),(x,y,z))
    def test_rim_and_wall_positive(self):
        for i in range(64):
            a=i*math.pi/32
            for r,z in [(3.9,5.99),(3.4,0),(2.91,-5.69)]:
                self.assertTrue(self.inside([r*math.cos(a),r*math.sin(a),z]),(i,r,z))
    def test_base_is_one_continuous_solid(self):
        for i in range(21):
            for j in range(21):
                x=(i-10)*.25;y=(j-10)*.25
                if x*x+y*y<2.75**2:
                    for z in [-5.99,-5.85,-5.71]:self.assertTrue(contains_point(self.hulls[0],[x,y,z]))
        self.assertFalse(self.inside([0,0,-6.01]))
    def test_no_cavity_bridge_and_bounded_chord_error(self):
        for i in range(128):
            a=i*math.pi/64
            for z in [-5.69,0,5.99]:
                inner=2.8+(z+5.7)/11.7
                self.assertFalse(self.inside([(inner-.025)*math.cos(a),(inner-.025)*math.sin(a),z]))
        self.assertLess(3.8*(1-math.cos(math.pi/32)),.019)
