import math
import unittest
from maker_arm_cup_clearance import lowest_clearance

class MeshClearance(unittest.TestCase):
    def table(self):
        return [(x,y,z) for x in (-40,40) for y in (-100,100) for z in (69,75)]

    def cup(self,angle,center_z):
        c,s=math.cos(angle),math.sin(angle)
        vertices=[]
        for radius,z in ((3.,-6.),(4.,6.),(2.8,-5.7),(3.8,6.)):
            for i in range(64):
                x=radius*math.cos(i*2*math.pi/64);y=radius*math.sin(i*2*math.pi/64)
                vertices.append((x,c*y-s*z,s*y+c*z+center_z))
        return vertices

    def test_resting_cup_and_lift(self):
        self.assertEqual(lowest_clearance(self.cup(0,81),self.table()),(0.,75))
        self.assertEqual(lowest_clearance(self.cup(0,93),self.table()),(12.,75))

    def test_tilted_taper_uses_actual_lower_ring_not_full_bounds(self):
        a=math.radians(10)
        clearance,_=lowest_clearance(self.cup(a,93),self.table())
        self.assertAlmostEqual(clearance,18-6*math.cos(a)-3*math.sin(a))
        self.assertGreater(clearance,18-6*math.cos(a)-4*math.sin(a))

    def test_invalid_geometry_fails_closed(self):
        for vertices in ([],[(0,0,float('nan'))]):
            with self.assertRaises(ValueError):lowest_clearance(vertices,self.table())
