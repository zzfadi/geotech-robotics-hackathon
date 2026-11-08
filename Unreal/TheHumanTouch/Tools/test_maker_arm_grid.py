import unittest
from maker_arm_grid import offset, shell, translation, ROOM_CM


class GridTests(unittest.TestCase):
    def test_original_and_ten_replicas_are_distinct_nonoverlapping_rooms(self):
        offsets = [offset(i) for i in range(11)]
        self.assertEqual(offsets[0], (0, 0, 0))
        self.assertEqual(offsets[3], (820, 0, 0))
        self.assertEqual(offsets[10], (2460, 820, 0))
        for i, a in enumerate(offsets):
            for b in offsets[i+1:]:
                self.assertTrue(abs(a[0]-b[0]) > ROOM_CM or abs(a[1]-b[1]) > ROOM_CM)

    def test_migration_uses_observed_floor_and_is_idempotent(self):
        self.assertEqual(translation(4, (45, 3200, -2)), (820, -2380, 0))
        self.assertEqual(translation(4, shell(4)['Floor']['location']), (0, 0, 0))

    def test_each_room_has_complete_cube_shell(self):
        for i in range(11):
            s = shell(i)
            self.assertEqual(len(s), 5)
            self.assertNotIn('Roof', s)
            self.assertEqual(s['LeftWall']['scale'][2], 8)
            self.assertEqual(s['BackWall']['location'][0]-s['RearWall']['location'][0], 800)

    def test_reject_invalid_table_identity(self):
        for index in (-1, 13, True, 1.5, '2'):
            with self.assertRaises(ValueError):
                offset(index)


if __name__ == '__main__':
    unittest.main()
