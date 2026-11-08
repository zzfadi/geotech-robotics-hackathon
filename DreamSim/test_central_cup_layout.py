import importlib.util
import itertools
import math
from pathlib import Path
import unittest
from fleet_planning import cup_layout

spec=importlib.util.spec_from_file_location('scene_cups',Path(__file__).resolve().parents[1]/'Unreal/TheHumanTouch/Tools/maker_arm_cups.py')
scene=importlib.util.module_from_spec(spec);spec.loader.exec_module(scene)

class CentralCupLayout(unittest.TestCase):
    def test_shared_contract_bounds_spacing_and_count(self):
        for seed in (0,1,7,20261107,157479489,2**32-1):
            total=0
            for index in range(9):
                layout=cup_layout(index,seed);total+=len(layout)
                self.assertEqual(len(layout),4+index%2)
                self.assertEqual([(o['label'],o['location_cm']) for o in layout],[(o['label'],o['location_cm']) for o in scene.cup_layout(index,seed)])
                for ordinal,o in enumerate(layout):
                    x,y,z=o['location_cm'];self.assertTrue(48<=x<=(52 if ordinal==4 else 62));self.assertTrue(-33.5<=y<=33.5);self.assertEqual(z,81)
                for a,b in itertools.combinations(layout,2):self.assertGreaterEqual(math.dist(a['location_cm'],b['location_cm']),13)
                self.assertLess(layout[2]['location_cm'][1],0)
            self.assertEqual(total,40)
    def test_randomization_changes_positions_but_stays_in_center(self):
        self.assertNotEqual(cup_layout(3,1),cup_layout(3,2))
