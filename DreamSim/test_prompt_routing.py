import unittest
from prompt_routing import parse_target


class PromptRoutingTests(unittest.TestCase):
    def test_display_numbers_map_to_internal_station(self):
        for number in range(1, 10):
            result = parse_target('Table %d: pick up the red cube' % number)
            self.assertEqual(result['route'], 'fleet')
            self.assertEqual(result['tables'], [number-1])
            self.assertEqual(result['prompt'], 'pick up the red cube')
        self.assertEqual(parse_target('Table 1: move left arm')['route'], 'fleet')

    def test_all_and_robot_aliases(self):
        self.assertEqual(parse_target('ALL TABLES: lift the cube')['tables'], 'all')
        self.assertEqual(parse_target('All robots pick up the orange cylinder')['prompt'], 'pick up the orange cylinder')
        self.assertEqual(parse_target('Robot #3, open the left gripper')['tables'], [2])

    def test_plain_prompt_defaults_to_fleet_table_zero(self):
        result = parse_target('Move the left robot arm toward the cube.')
        self.assertFalse(result['explicit'])
        self.assertEqual(result['tables'], [0])
        self.assertEqual(result['route'], 'fleet')

    def test_rejects_invalid_or_conflicting_addresses(self):
        for text in ('Table 0: lift', 'Table 10: lift', 'Table 11: lift', 'Table -1: lift', 'Table two: lift', 'Table 3:',
                     'Table 2 and table 3: lift', 'Table 3, 4: lift',
                     'Move table 3 to the cube', 'T03: lift', 'All tables: table 2 lift'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_target(text)


if __name__ == '__main__':
    unittest.main()
