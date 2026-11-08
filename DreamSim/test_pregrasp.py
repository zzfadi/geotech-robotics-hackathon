import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'Unreal/TheHumanTouch/Tools'))
import pregrasp as pg  # noqa: E402
from maker_arm_fleet_layout import label, offset, normalize_config  # noqa: E402

CUP = [63.64494022277172, -40.75709821687124, 81.0]


def table(index=0, **extra):
    return {'index': index, 'prompt': 'p', 'target': {'label': 'MakerStation_Prop_Cup', 'location_cm': CUP},
            'objective': {'kind': 'lift_hold', 'target': 'MakerStation_Prop_Cup', 'side': 'left', 'height_cm': 15,
                          'hold_seconds': 5, 'max_tilt_deg': 15}, **extra}


class Pregrasp(unittest.TestCase):
    def test_start_pose_matches_verified_mechanical_grasp(self):
        state = pg.pregrasp_state(table())
        f = pg._fk().pose(state['q_left'])
        # Codex's zero-API mechanical cup grasp put the tool here (arm frame, metres).
        np.testing.assert_allclose(f[:3, 3], [.4364, .0076, .075], atol=1e-3)
        self.assertEqual(state['gap_left_m'], .1)
        self.assertGreater(pg.table_clearance(state['q_left'], .1, 'left'), 3)

    def test_guard_raises_rows_into_the_table_and_leaves_others(self):
        state = {**pg.pregrasp_state(table()), 'q_right': [0]*6, 'gap_right_m': .1}
        q = state['q_left']
        f = pg._fk().pose(q)
        low = pg.solve_pose(f[:3, 3]-np.array([0, 0, .075]), f[:3, :3], q)
        self.assertLess(pg.table_clearance(low, .1, 'left'), 0)
        action = {'q_l_chunk': [q+[.95], low+[.95]], 'q_r_chunk': [[0]*6+[.95]]*2}
        out, report = pg.guard_table(action, state, 'left')
        self.assertEqual(out['q_l_chunk'][0], q+[.95])
        self.assertEqual([r['row'] for r in report['raised_rows']], [1])
        self.assertGreaterEqual(pg.table_clearance(out['q_l_chunk'][1][:6], .1, 'left'), .3)

    def test_guard_accepts_dream_grip_slightly_past_fully_open(self):
        state = {**pg.pregrasp_state(table()), 'q_right': [0]*6, 'gap_right_m': .1}
        q = state['q_left']
        out, report = pg.guard_table({'q_l_chunk': [q+[1.0035]], 'q_r_chunk': [[0]*6+[1.0035]]}, state, 'left')
        self.assertEqual(report['raised_rows'], [])

    def test_vertical_only_keeps_xy_and_orientation(self):
        state = {**pg.pregrasp_state(table()), 'q_right': [0]*6, 'gap_right_m': .1}
        q = state['q_left']
        fk = pg._fk()
        f = fk.pose(q)
        rows = [pg.solve_pose(f[:3, 3]+np.array([.002*i, .003*i, .0025*i]), f[:3, :3], q)+[.33] for i in range(12)]
        out, report = pg.vertical_only({'q_l_chunk': rows, 'q_r_chunk': [[0]*6+[.95]]*12}, state, 'left')
        end = fk.pose(out['q_l_chunk'][-1][:6])
        np.testing.assert_allclose(end[:2, 3], f[:2, 3], atol=2e-3)
        self.assertAlmostEqual(end[2, 3]-f[2, 3], .0275, delta=.003)
        self.assertEqual(len(out['q_l_chunk']), len(out['q_r_chunk']))

    def test_run_config_resolves_start_and_resets_target_upright(self):
        config = {'defaults': {'assist': {'start': 'pregrasp'}}, 'tables': [table(objects=[
            {'label': 'MakerStation_Prop_Cup_02', 'location_cm': [60, 30, 81]}])]}
        out = pg.prepare_run_config(config, config)
        variant = normalize_config(out)[0]
        self.assertEqual(variant['target']['rotation_deg'], [0, 0, 0])
        self.assertEqual(variant['assist']['resolved_start']['target_cm'], CUP)
        self.assertEqual([o['label'] for o in variant['objects']], ['MakerStation_Prop_Cup_02'])
        untouched = pg.prepare_run_config({'tables': [table()]}, None)
        self.assertNotIn('initial_state', untouched['tables'][0])

    def test_live_layout_uses_editor_positions_per_table(self):
        config = {'defaults': {'assist': {'start': 'pregrasp', 'layout': 'live'}}, 'tables': [table(1)]}
        d = offset(1)
        world = {label(1, 'MakerStation_Prop_Cup'): [55+d[0], -31+d[1], 81+d[2]],
                 label(1, 'MakerStation_Prop_Cup_02'): [60+d[0], 30+d[1], 81+d[2]],
                 'MakerStation_Prop_Cup': [1, 2, 81]}
        variant = normalize_config(pg.apply_live_layout(config, world))[0]
        np.testing.assert_allclose(variant['target']['location_cm'], [55, -31, 81], atol=1e-6)
        self.assertEqual(len(variant['objects']), 1)
        self.assertIn("Path('/x/live.json')", pg.live_cups_expression('/x/live.json'))


if __name__ == '__main__':
    unittest.main()
