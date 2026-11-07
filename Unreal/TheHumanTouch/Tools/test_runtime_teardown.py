"""Exercise production teardown methods without loading Unreal's Python runtime."""
import ast
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

TOOLS=Path(__file__).parent

def method(filename,cls,name,namespace):
    tree=ast.parse((TOOLS/filename).read_text())
    group=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls)
    node=next(n for n in group.body if isinstance(n,ast.FunctionDef) and n.name==name)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),filename,'exec'),namespace)
    return namespace[name]

class TeardownTests(unittest.TestCase):
    def exercise_stop(self,fail=False):
        calls=[]
        def mirror_finish(commit):
            calls.append('mirror-close')
            if fail:raise RuntimeError('mirror rejected final sample')
        stop=method('maker_arm_runtime.py','Session','stop',{
            'maker_arm_observer':SimpleNamespace(stop=lambda:calls.append('observer-stop')),
            'time':SimpleNamespace(monotonic=lambda:1.)})
        obj=SimpleNamespace(phase='ready',error=None,motion=None,mirror=SimpleNamespace(finish=mirror_finish),
                            report=lambda:calls.append('report'),level=SimpleNamespace(editor_request_end_play=lambda:calls.append('end-SIE')))
        stop(obj,True)
        return obj,calls
    def test_mirror_closes_before_observer_and_world_teardown(self):
        obj,calls=self.exercise_stop()
        self.assertEqual(calls,['mirror-close','observer-stop','report','end-SIE'])
        self.assertTrue(obj.commit)
    def test_failed_final_mirror_still_ends_simulation_without_commit(self):
        obj,calls=self.exercise_stop(True)
        self.assertEqual(calls,['mirror-close','observer-stop','report','end-SIE'])
        self.assertFalse(obj.commit)
        self.assertIn('rejected',obj.error)
    def test_runtime_observer_never_deletes_runtime_actors(self):
        calls=[]
        stop=method('maker_arm_observer.py','Observer','stop',{
            'unreal':SimpleNamespace(unregister_slate_post_tick_callback=lambda h:calls.append('unregister')),
            'call_camera':SimpleNamespace(_remove=lambda actors:calls.append('delete')),
            'json':json})
        with TemporaryDirectory() as folder:
            obj=SimpleNamespace(handle=1,world=object(),cameras={'overhead':{'actor':object()}},directory=Path(folder),frames=8)
            stop(obj)
            self.assertEqual(calls,['unregister'])
            self.assertEqual(json.loads((Path(folder)/'status.json').read_text()),{'running':False,'frames':8})

if __name__=='__main__':unittest.main()
