"""Offline hook order/default isolation, independent of Unreal availability."""
import ast
import importlib.util
from pathlib import Path
import unittest
TOOLS=Path(__file__).resolve().parents[1]/'Unreal/TheHumanTouch/Tools'
spec=importlib.util.spec_from_file_location('expert',TOOLS/'maker_arm_expert_capture.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class HookTests(unittest.TestCase):
 def test_packet_copies_measured_arrays(self):
  state={'q_left':[1]*6,'q_right':[2]*6,'gap_left_m':.04,'gap_right_m':.05,'other':'omit'}
  out=mod.joint_packet(state);state['q_left'][0]=9
  self.assertEqual(out['q_left'][0],1);self.assertEqual(set(out),set(mod.STATE_KEYS))
 def test_ack_is_distinct_timestamp_after_application(self):
  class Recorder:
   closed=False
   def acknowledge(self,p,t):self.packet=p;self.time=t
  hook=object.__new__(mod.ExpertCapture);hook.recorder=Recorder()
  state={'q_left':[1]*6,'q_right':[2]*6,'gap_left_m':.04,'gap_right_m':.05}
  hook.after_control(state);self.assertEqual(hook.recorder.packet,state);self.assertGreater(hook.recorder.time,0)
 def test_default_and_control_order(self):
  tree=ast.parse((TOOLS/'maker_arm_fleet_runtime.py').read_text())
  session=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Session')
  tick=next(n for n in session.body if isinstance(n,ast.FunctionDef) and n.name=='_tick')
  calls=[(n.lineno,n.func.attr) for n in ast.walk(tick) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
  names=[name for _,name in sorted(calls)]
  self.assertLess(names.index('before_control'),names.index('step_motion'))
  self.assertLess(names.index('step_motion'),names.index('after_control'))
  self.assertIn('self.expert = None',(TOOLS/'maker_arm_fleet_runtime.py').read_text())
if __name__=='__main__':unittest.main()
