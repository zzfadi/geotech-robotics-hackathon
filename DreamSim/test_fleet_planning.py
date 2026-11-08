import math
import unittest
from fleet_planning import build_plan, apply_plan


class Planning(unittest.TestCase):
    def test_seed_replays_and_subsets_preserve_experiments(self):
        full = build_plan(list(range(1, 11)), mode='random', seed=42, randomize=True)
        self.assertEqual(full, build_plan(list(range(1, 11)), mode='random', seed=42, randomize=True))
        self.assertEqual(full['tables'][4], build_plan([5], mode='random', seed=42, randomize=True)['tables'][0])
        self.assertGreater(len({t['prompt'] for t in full['tables']}), 1)

    def test_objects_have_clearance_and_remain_on_table(self):
        for seed in range(50):
            for table in build_plan([1, 5, 10], mode='random', seed=seed, randomize=True)['tables']:
                for i, obj in enumerate(table['objects']):
                    x, y, z = obj['location_cm']; r = obj['clearance_radius_cm']
                    self.assertTrue(5+r < x < 85-r)
                    self.assertTrue(-100+r < y < 100-r)
                    self.assertGreater(z, 75)
                    for other in table['objects'][i+1:]:
                        ox, oy, _ = other['location_cm']
                        self.assertGreater(math.hypot(x-ox, y-oy), r+other['clearance_radius_cm'])

    def test_shared_prompt_and_independent_config_copy(self):
        config = {'defaults': {'cycles': 12}, 'tables': [{'name': 'a'}, {'name': 'b'}]}
        plan = build_plan([1, 2], prompt='Pick up red cube', seed=3)
        result = apply_plan(config, plan)
        self.assertEqual([t['prompt'] for t in result['tables']], ['Pick up red cube']*2)
        self.assertNotIn('prompt', config['tables'][0])
        self.assertIsNone(plan['tables'][0]['objective'])  # Free text is not a verified lift objective.

    def test_input_validation(self):
        for indices in ([True], [0], [13], [1, 1], []):
            with self.assertRaises(ValueError):
                build_plan(indices, prompt='lift')
        for seed in (-1, 2**32, True, '3'):
            with self.assertRaises(ValueError):
                build_plan([1], prompt='lift', seed=seed)


if __name__ == '__main__':
    unittest.main()
