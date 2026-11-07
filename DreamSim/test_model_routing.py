import unittest
from types import SimpleNamespace
from model_routing import ModelBudget,FLASH,FULL,red_cube_lift

class RoutingTests(unittest.TestCase):
    def test_flash_approach_and_progress(self):
        b=ModelBudget()
        self.assertEqual(b.choose(7,0),FLASH)
        self.assertEqual(b.choose(9,2),FLASH)
        self.assertEqual(b.choose(9,None),FLASH)
    def test_actual_calls_bounded_including_retries(self):
        b=ModelBudget()
        for _ in range(2):
            self.assertEqual(b.choose(8,0),FULL);b.charge(FULL)
        self.assertEqual(b.choose(10,0),FLASH)
        with self.assertRaises(RuntimeError):b.charge(FULL)
    def test_capacity_disables_full_for_episode_only(self):
        b=ModelBudget();e=SimpleNamespace(code='DA-2000',reason='pool_full')
        self.assertFalse(b.capacity_fallback(FLASH,e))
        self.assertTrue(b.capacity_fallback(FULL,e))
        self.assertEqual(b.choose(9,0),FLASH)
        self.assertEqual(ModelBudget().choose(9,0),FULL)
    def test_other_failures_not_hidden(self):
        self.assertFalse(ModelBudget().capacity_fallback(FULL,SimpleNamespace(code='DA-5000',reason=None)))
    def test_target_only(self):
        self.assertIsNone(red_cube_lift({'props':{}}))
        self.assertEqual(red_cube_lift({'props':{'MakerStation_Approximate_Block_Red':{'max_z_cm':80,'initial_z_cm':78}}}),2)
if __name__=='__main__':unittest.main()
