import itertools
import math
import unittest
from maker_arm_contact_stop import contact_gap,pad_points,inner_pad_offset

def box(center=(0,0,-.13),size=.05):
    return [('cube',list(itertools.product(*[(v-size/2,v+size/2) for v in center])))]

class ContactStop(unittest.TestCase):
    def test_empty_closes(self):self.assertEqual(contact_gap(.01,.06,[]),(.01,[]))
    def test_centered_cube(self):self.assertAlmostEqual(contact_gap(.01,.06,box())[0],.035)
    def test_opening(self):self.assertEqual(contact_gap(.08,.045,box()),(.08,[]))
    def test_outside_corridor(self):
        for center in ((.2,0,-.13),(0,.1,-.13),(0,0,-.3)):
            self.assertEqual(contact_gap(.01,.06,box(center)),(.01,[]))
    def test_smaller_cube(self):self.assertAlmostEqual(contact_gap(0,.04,box(size=.03))[0],.015)
    def test_never_forces_open(self):self.assertEqual(contact_gap(.01,.02,box())[0],.02)
    def test_held_gap(self):self.assertAlmostEqual(contact_gap(.01,.045,box())[0],.035)
    def test_rotated_box_width_uses_only_contact_region(self):
        # Long diagonal object: its far corners must not hold the jaws open.
        c=math.sqrt(.5)
        points=[(c*x-c*y,c*x+c*y,z-.13) for x,y,z in itertools.product((-.1,.1),(-.01,.01),(-.02,.02))]
        projected=max(p[1] for p in points)-min(p[1] for p in points)
        clipped=pad_points(points)
        width=max(p[1] for p in clipped)-min(p[1] for p in clipped)
        self.assertLess(width,projected/2)
        self.assertAlmostEqual(contact_gap(0,.104825,[('rod',points)])[0],width-.015)

    def test_flat_pad_excludes_object_above_actual_pad(self):
        self.assertEqual(contact_gap(.01,.06,box((0,0,-.13),size=.03),flat=True),(.01,[]))

    def test_flat_pad_has_zero_inner_surface_offset(self):
        self.assertAlmostEqual(contact_gap(.01,.06,box((0,0,-.17)),flat=True)[0],.045)

    def test_cad_pad_offset(self):
        self.assertAlmostEqual(inner_pad_offset(-.13),.005)
        self.assertAlmostEqual(inner_pad_offset(-.14),.00237)
        self.assertEqual(inner_pad_offset(-.16),0)

if __name__=='__main__':unittest.main()
