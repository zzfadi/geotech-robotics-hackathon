"""Run with the validation script's numpy/scipy/trimesh/rtree dependencies."""
import unittest
import numpy as np
import trimesh
from validate_maker_arm_surfaces import outside_distances

class SurfaceTests(unittest.TestCase):
    def test_convex_fill_of_a_real_hole_is_detected(self):
        ring=trimesh.creation.annulus(r_min=10,r_max=20,height=4,sections=64)
        points=np.array([[0,0,0],[15,0,0],[15,0,4],[25,0,0]])
        distance=outside_distances(ring,points)
        # A convex hull of this ring covers every source vertex but fills its hole.
        self.assertGreater(distance[0],9.9)
        self.assertEqual(distance[1],0)
        self.assertAlmostEqual(distance[2],2,places=6)
        self.assertAlmostEqual(distance[3],5,places=6)

if __name__=='__main__': unittest.main()
