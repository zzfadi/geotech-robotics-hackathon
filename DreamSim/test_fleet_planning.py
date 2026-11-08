import math
import unittest
from fleet_planning import build_plan, apply_plan


class Planning(unittest.TestCase):
    def test_seed_replays_and_subsets_preserve_experiments(self):
        full = build_plan(list(range(9)), mode='random', seed=42, randomize=True)
        self.assertEqual(full, build_plan(list(range(9)), mode='random', seed=42, randomize=True))
        self.assertEqual(full['tables'][5], build_plan([5], mode='random', seed=42, randomize=True)['tables'][0])
        self.assertGreater(len({tuple(t['objects'][0]['location_cm']) for t in full['tables']}), 1)
        self.assertTrue(all(t['target']=='MakerStation_Prop_Cup' for t in full['tables']))
        self.assertTrue(all(len(t['objects'])==1 for t in full['tables']))
        self.assertTrue(all(t['objective']['hold_seconds']==5 for t in full['tables']))

    def test_objects_have_clearance_and_remain_on_table(self):
        for seed in range(50):
            for table in build_plan([0, 5, 8], mode='random', seed=seed, randomize=True)['tables']:
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
        plan = build_plan([0, 1], prompt='Pick up the drinking cup', seed=3)
        result = apply_plan(config, plan)
        self.assertEqual([t['prompt'] for t in result['tables']], ['Pick up the drinking cup']*2)
        self.assertNotIn('prompt', config['tables'][0])
        self.assertIsNone(plan['tables'][0]['objective'])  # Free text is not a verified lift objective.

    def test_commanded_target_survives_randomized_layout_without_inherited_geometry(self):
        config={'defaults':{},'tables':[{'index':i,'target':{'label':'old-object','scale':[1,1,3],'location_cm':[1,2,3]}}
                                       for i in range(9)]}
        for seed in range(10):
            plan=build_plan(list(range(9)),mode='random',seed=seed,randomize=True)
            applied=apply_plan(config,plan)
            for task,variant in zip(plan['tables'],applied['tables']):
                self.assertEqual(task['target'],task['objective']['target'])
                self.assertEqual(task['target'],variant['target']['label'])
                self.assertIsNone(variant['target']['scale'])
                self.assertIsNone(variant['target']['location_cm'])
                target=next(o for o in task['objects'] if o['label']==task['target'])
                self.assertEqual(task['objective']['side'],'left' if target['location_cm'][1]<0 else 'right')
                self.assertIn('Use the '+task['objective']['side']+' arm',task['prompt'])
                self.assertEqual(variant['objective'],task['objective'])

    def test_configured_fixture_preserves_objective_layout_and_pose(self):
        fixture={'index':1,'prompt':'Lift the drinking cup','objective':{'kind':'lift_hold','target':'MakerStation_Prop_Cup','height_cm':10},
                 'objects':[{'label':'MakerStation_Prop_Cup','location_cm':[40,-40,77.5]}],
                 'target':{'label':'MakerStation_Prop_Cup','location_cm':[40,-40,77.5]},
                 'initial_state':{'q_left':[1,2,3,4,5,6]}}
        config={'defaults':{},'tables':[fixture]}
        plan=build_plan([1],mode='configured',seed=7)
        self.assertEqual(apply_plan(config,plan)['tables'][0],fixture)
        randomized=apply_plan(config,build_plan([1],mode='configured',seed=7,randomize=True))['tables'][0]
        self.assertEqual(randomized['objective'],fixture['objective'])
        self.assertEqual(randomized['target'],fixture['target'])
        self.assertEqual(len(randomized['objects']),1)
        self.assertEqual(randomized['initial_state']['q_left'],[0]*6)
        self.assertEqual(config['tables'][0],fixture)

    def test_input_validation(self):
        for indices in ([True], [-1], [9], [1, 1], []):
            with self.assertRaises(ValueError):
                build_plan(indices, prompt='lift')
        for seed in (-1, 2**32, True, '3'):
            with self.assertRaises(ValueError):
                build_plan([1], prompt='lift', seed=seed)


if __name__ == '__main__':
    unittest.main()
