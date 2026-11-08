import importlib.util,sys,types,unittest
from pathlib import Path
from unittest.mock import patch
P=Path(__file__).resolve().parents[1]/'Unreal/TheHumanTouch/Tools/maker_arm_wrist_framing.py'
spec=importlib.util.spec_from_file_location('framing',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Source:
 def __init__(self,label):self.label=label
 def get_actor_label(self):return self.label
 def get_actor_rotation(self):return 'original'
class Tests(unittest.TestCase):
 def test_both_sides_and_replicas_same_basis(self):
  fake=types.SimpleNamespace(Vector=lambda *v:v,MathLibrary=types.SimpleNamespace(make_rot_from_xy=lambda f,r:(f,r)))
  with patch.dict(sys.modules,unreal=fake):
   for prefix in ('','MakerFleet_T08__'):
    for side in ('Left','Right'):
     forward,right=m.capture_rotation(Source(prefix+'MakerCamera_'+side+'Wrist'))
     self.assertEqual(forward,(0,0,-1));self.assertEqual(right,(0,1,0))
     # Camera right cross up = forward; up=world +X, determinant +1.
     up=(right[1]*forward[2]-right[2]*forward[1],right[2]*forward[0]-right[0]*forward[2],right[0]*forward[1]-right[1]*forward[0])
     # Forward cross right yields image up in the UE camera basis.
     up=tuple(-v for v in up)
     self.assertEqual(up,(1,0,0))
 def test_overhead_preserved(self):
  self.assertEqual(m.capture_rotation(Source('MakerCamera_Overview')),'original')
  self.assertEqual(m.capture_rotation(Source('MakerInteraction_T09__MakerCamera_LeftWrist')),'original')
if __name__=='__main__':unittest.main()
