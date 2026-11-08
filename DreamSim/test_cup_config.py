import json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'Unreal/TheHumanTouch/Tools'))
from maker_arm_fleet_layout import normalize_config
class CupConfig(unittest.TestCase):
 def test_only_cups_on_nine_tables(self):
  rows=normalize_config(json.loads((Path(__file__).parent/'fleet_cups.json').read_text()))
  self.assertEqual([r['index'] for r in rows],list(range(9)))
  for r in rows:
   self.assertEqual(r['target']['label'],'MakerStation_Prop_Cup')
   self.assertEqual(len(r['objects']),1+r['index']%2)
   self.assertEqual({o['label'] for o in r['objects']}, {'MakerStation_Prop_Cup_02'} | ({'MakerStation_Prop_Cup_03'} if r['index']%2 else set()))
   self.assertEqual(r['objective']['hold_seconds'],5)
   self.assertEqual(r['objective']['max_tilt_deg'],15)
   self.assertEqual(r['initial_state']['gap_left_m'],.1)
