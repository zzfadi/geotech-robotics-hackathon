"""Exercise the real startup routine without importing Unreal outside its editor."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

class ContactStartupTests(unittest.TestCase):
    def run_start(self, fail=False):
        tree=ast.parse(Path(__file__).with_name('maker_arm_fleet_runtime.py').read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Session')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='initialize_contacts')
        scope={'BASES':{'left':None,'right':None},'JAW_MAX_M':.104825,
               'unreal':SimpleNamespace(CollisionEnabled=SimpleNamespace(QUERY_AND_PHYSICS=3))}
        exec(compile(ast.Module(body=[method],type_ignores=[]),'<actual startup>','exec'),scope)
        events=[]
        def pose(table,side,row):
            events.append(('pose',side,row))
            if fail and side=='right':raise ValueError('invalid pose')
        proxy=SimpleNamespace(static_mesh_component=SimpleNamespace(set_collision_enabled=lambda v:events.append(('enable',v))))
        table=SimpleNamespace(state={'q_left':[0]*6,'q_right':[1]*6,'gap_left_m':.1,'gap_right_m':.08},proxies={'l':proxy,'r':proxy})
        try:scope['initialize_contacts'](SimpleNamespace(apply_row=pose),table)
        except ValueError:
            if not fail:raise
        return events
    def test_positions_both_sides_before_enabling_any_contact(self):
        events=self.run_start()
        self.assertEqual([e[0] for e in events],['pose','pose','enable','enable'])
        self.assertAlmostEqual(events[0][2][6],.1/.104825)
        self.assertAlmostEqual(events[1][2][6],.08/.104825)
    def test_invalid_pose_never_enables_contacts(self):
        self.assertEqual([e[0] for e in self.run_start(True)],['pose','pose'])

if __name__=='__main__':unittest.main()
