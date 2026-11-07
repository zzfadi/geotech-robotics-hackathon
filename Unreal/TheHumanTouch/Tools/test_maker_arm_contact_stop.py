import itertools
import unittest
from maker_arm_contact_stop import contact_gap

def box(center=(0,0,-.13),size=.05):
    return [('cube',list(itertools.product(*[(v-size/2,v+size/2) for v in center])))]

class ContactStop(unittest.TestCase):
    def test_empty_closes(self):self.assertEqual(contact_gap(.01,.06,[]),(.01,[]))
    def test_centered_cube(self):self.assertAlmostEqual(contact_gap(.01,.06,box())[0],.045)
    def test_opening(self):self.assertEqual(contact_gap(.08,.045,box()),(.08,[]))
    def test_outside_corridor(self):
        for center in ((.2,0,-.13),(0,.1,-.13),(0,0,-.3)):
            self.assertEqual(contact_gap(.01,.06,box(center)),(.01,[]))
    def test_smaller_cube(self):self.assertAlmostEqual(contact_gap(0,.04,box(size=.03))[0],.025)
    def test_never_forces_open(self):self.assertEqual(contact_gap(.01,.02,box())[0],.02)
    def test_held_gap(self):self.assertAlmostEqual(contact_gap(.01,.045,box())[0],.045)
if __name__=='__main__':unittest.main()
