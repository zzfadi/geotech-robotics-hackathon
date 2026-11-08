import ast
import json
import unittest
from pathlib import Path

import maker_arm_fleet_layout as layout

HERE = Path(__file__).resolve().parent
VARIANTS = HERE.parents[2] / 'DreamSim/fleet_variants.json'


class Layout(unittest.TestCase):
    def test_target_rotation_preserves_angles_and_rejects_nonfinite(self):
        variant = {'prompt': 'pickup', 'target': {'rotation_deg': [90, -12.5, 4]}}
        self.assertEqual(layout.normalize_variant(variant, 1)['target']['rotation_deg'], [90., -12.5, 4.])
        for angles in ([0, float('nan'), 0], [0, 0, float('inf')], [0, 0]):
            with self.assertRaises(ValueError):
                layout.normalize_variant({'prompt': 'pickup', 'target': {'rotation_deg': angles}}, 1)

    def test_labels_round_trip(self):
        name = layout.label(3, 'MakerArm_left_link_007')
        self.assertEqual(name, 'MakerFleet_T03__MakerArm_left_link_007')
        self.assertEqual(layout.parse_label(name), (3, 'MakerArm_left_link_007'))
        self.assertIsNone(layout.parse_label('MakerArm_left_link_007'))

    def test_replica_labels_never_match_original_station_filters(self):
        # Codex's runtime/observer/feedback select by these exact prefixes.
        for prefix in ('MakerArm_', 'MakerStation_Approximate_Block_', 'DreamSim_RuntimeProxy_',
                       'DreamSim_LocalCapture_', 'MakerCamera_'):
            self.assertFalse(layout.label(1, prefix + 'x').startswith(prefix))

    def test_table_zero_is_reserved_for_original(self):
        self.assertEqual(layout.offset(0), (0, 0, 0))
        self.assertEqual(layout.label(0, 'MakerArm_left_link_007'), 'MakerArm_left_link_007')
        with self.assertRaises(ValueError):
            layout.offset(layout.MAX_TABLES + 1)

    def test_booths_use_three_column_grid(self):
        from maker_arm_grid import offset
        for i in range(1, layout.MAX_TABLES+1):
            self.assertEqual(layout.offset(i), offset(i))
        self.assertEqual(layout.offset(3), (820, 0, 0))

    def test_neighbour_unshadowed_fills_reach_at_most_the_radius_edge(self):
        # maker_arm_lighting: unshadowed SoftFill/BackFill, 700 cm attenuation radius.
        table = (45, 0, 75)
        for light in ((40, 120, 220), (-65, 0, 200)):
            for neighbour in (-1, 1):
                p = (light[0], light[1] + neighbour*layout.SPACING_CM, light[2])
                distance = sum((a-b)**2 for a, b in zip(p, table))**.5
                self.assertGreater(distance, 690)

    def test_source_filter(self):
        self.assertTrue(layout.is_source_label('MakerArm_left_link_007'))
        self.assertTrue(layout.is_source_label('MakerEnvironment_RightWall'))
        self.assertTrue(layout.is_source_label('Dreamlabs_YAM_Table'))
        self.assertTrue(layout.is_source_label('MakerEnvironment_LeftWall'))
        for skipped in ('Dreamlabs_YAM_left_link1', 'Dreamlabs_YAM_right_tip_left',
                        'Dreamlabs_YAM_FillLight', 'MakerFleet_T01__MakerArm_left_link_007', 'BP_Performer'):
            self.assertFalse(layout.is_source_label(skipped), skipped)

    def test_bases_match_station(self):
        tree = ast.parse((HERE/'yam_station.py').read_text())
        bases = next(ast.literal_eval(n.value) for n in tree.body
                     if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == 'BASES')
        self.assertEqual({k: tuple(v) for k, v in bases.items()}, layout.STATION_BASES)
        self.assertEqual(layout.replica_base(2, bases['left']), (20, -40 + 1640, 76))


class Variants(unittest.TestCase):
    def test_shipped_matrix_is_valid(self):
        tables = layout.normalize_config(json.loads(VARIANTS.read_text()))
        self.assertEqual(len(tables), 9)
        self.assertEqual([t['index'] for t in tables], list(range(9)))
        self.assertEqual(len({t['name'] for t in tables}), 9)
        for t in tables:
            self.assertEqual(t['target']['label'], layout.TARGET)
            self.assertEqual(t['target']['location_cm'], [60.0, -40.0, 78.5])
        self.assertFalse(tables[1]['position_ik'])
        self.assertEqual(tables[5]['initial_state']['q_left'], [0.0]*6)

    def test_rejects_bad_fields(self):
        base = {'prompt': 'Pick up the red cube.'}
        for bad in ({'checkpoint': 'other'}, {'action_rows': 0}, {'cycles': 0}, {'tcp_back': .05},
                    {'position_ik': True, 'tcp_back': .01}, {'target': {'label': 'MakerArm_left_base_link'}},
                    {'initial_state': {'q_left': [0]*5, 'q_right': [0]*6, 'gap_left_m': .04, 'gap_right_m': .04}},
                    {'initial_state': {'q_left': [0]*6, 'q_right': [0]*6, 'gap_left_m': .2, 'gap_right_m': .04}},
                    {'prompt': ' '}):
            with self.assertRaises(ValueError, msg=str(bad)):
                layout.normalize_variant({**base, **bad}, 1)

    def test_target_defaults_merge_per_field(self):
        v = layout.normalize_variant({'target': {'scale': [.07, .07, .07]}}, 2,
                                     {'prompt': 'p', 'target': {'location_cm': [1, 2, 3]}})
        self.assertEqual(v['target']['location_cm'], [1.0, 2.0, 3.0])
        self.assertEqual(v['target']['scale'], [.07, .07, .07])

    def test_table_limit(self):
        with self.assertRaises(ValueError):
            layout.normalize_config({'tables': []})
        with self.assertRaises(ValueError):
            layout.normalize_config({'defaults': {'prompt': 'p'}, 'tables': [{}]*(layout.MAX_TABLES+1)})


if __name__ == '__main__':
    unittest.main()
