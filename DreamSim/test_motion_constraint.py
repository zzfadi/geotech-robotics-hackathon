import copy
import unittest
from fleet_planning import build_plan, apply_plan, explicit_motion_side
from fleet_worker import constrain_motion_side
from maker_arm_fleet_layout import normalize_config, JAW_MAX_M


class MotionConstraint(unittest.TestCase):
    def test_custom_prompt_keeps_constraint_without_fabricating_objective(self):
        config = {'defaults': {}, 'tables': [{'index': 0, 'prompt': 'old',
                   'motion_side': 'right', 'objective': {'side': 'right'}}]}
        before = copy.deepcopy(config)
        plan = build_plan([0], prompt='Please pick up that cup. Keep the right arm still.')
        variant = normalize_config(apply_plan(config, plan))[0]
        self.assertIsNone(variant['objective'])
        self.assertEqual(variant['motion_side'], 'left')
        self.assertEqual(config, before)
        self.assertEqual(explicit_motion_side('Keep left arm still; wave hello'), 'right')

    def test_unknown_or_conflicting_text_does_not_inherit_side(self):
        config = {'tables': [{'index': 0, 'prompt': 'old', 'motion_side': 'left'}]}
        for text in ('wave hello', 'Use both arms. Keep right arm still.',
                     'Do not keep right arm still.', 'Keep right arm still. Move right arm.',
                     'Keep left arm still. Keep right arm still.'):
            variant = normalize_config(apply_plan(config, build_plan([0], prompt=text)))[0]
            self.assertIsNone(variant['motion_side'])
            self.assertIsNone(variant['objective'])

    def test_configured_and_standard_task_sides_validated(self):
        config = {'tables': [{'index': 0, 'prompt': 'old', 'objective': {'side': 'left'}}]}
        variant = normalize_config(apply_plan(config, build_plan([0], mode='configured')))[0]
        self.assertEqual(variant['motion_side'], 'left')
        task = build_plan([0], prompt='Hold my beer')['tables'][0]
        self.assertEqual(task['motion_side'], 'left')
        with self.assertRaises(ValueError):
            normalize_config({'tables': [{'prompt': 'test', 'motion_side': 'typo'}]})

    def test_direct_joint_policy_holds_inactive_arm_without_altering_active(self):
        raw = {'q_l_chunk': [[.1]*7, [.2]*7], 'q_r_chunk': [[999]*7]*2}
        original = copy.deepcopy(raw)
        state = {'q_right': [.3]*6, 'gap_right_m': .05}
        masked = constrain_motion_side(raw, state, 'left')
        self.assertEqual(masked['q_l_chunk'], raw['q_l_chunk'])
        self.assertEqual(masked['q_r_chunk'], [[.3]*6+[.05/JAW_MAX_M]]*2)
        self.assertEqual(raw, original)
