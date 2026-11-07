"""Offline lifecycle checks; these do not prove Unreal/Concert replication."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


def pose(x):
    return types.SimpleNamespace(translation=types.SimpleNamespace(x=x,y=0,z=0),
        rotation=types.SimpleNamespace(x=0,y=0,z=0,w=1),
        scale3d=types.SimpleNamespace(x=1,y=1,z=1))


class Actor:
    def __init__(self, label, x):
        self.label,self.pose,self.root_component=label,pose(x),object()
    def get_actor_label(self):return self.label
    def get_actor_transform(self):return self.pose
    def set_actor_transform(self,p,*args):self.pose=p


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.runtime,self.target=Actor('arm',10),Actor('arm',0)
        self.game,self.editor=object(),object()
        self.closed,self.snapshots=0,0
        self.sub=types.SimpleNamespace(get_game_world=lambda:self.game,get_editor_world=lambda:self.editor)
        self.task={'id':'test','transactions':0}
        def end():self.closed+=1
        def snap(obj):self.snapshots+=1
        u=types.SimpleNamespace(UnrealEditorSubsystem=object,Actor=Actor,Text=str,
            get_editor_subsystem=lambda cls:self.sub,
            GameplayStatics=types.SimpleNamespace(get_all_actors_of_class=lambda w,c:[self.runtime] if w is self.game else [self.target]),
            SystemLibrary=types.SimpleNamespace(begin_transaction=lambda *a:0,end_transaction=end,
                transact_object=lambda a:None,snapshot_object=snap,is_valid=lambda a:True),
            register_slate_post_tick_callback=lambda cb:cb,unregister_slate_post_tick_callback=lambda cb:None)
        tracking=types.SimpleNamespace(current_task=self.task,undo_count=lambda:self.closed,transaction_title=lambda s:s)
        cp=types.SimpleNamespace(tracking=tracking,animation=types.SimpleNamespace(is_running=lambda:False))
        self.modules=patch.dict(sys.modules,{'unreal':u,'copresence':cp});self.modules.start()
        spec=importlib.util.spec_from_file_location('mirror_test_subject',Path(__file__).with_name('maker_arm_runtime_mirror.py'))
        self.mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.mod)
    def tearDown(self):self.modules.stop()
    def mirror(self):return self.mod.RuntimeMirror(self.game,['arm'])
    def test_measured_pose_and_abort_restore(self):
        m=self.mirror();m.tick();self.assertEqual(self.target.pose.translation.x,10)
        m.finish(False);self.assertEqual(self.target.pose.translation.x,0)
        self.assertEqual(self.snapshots,2);self.assertEqual(self.closed,1)
        m.finish();self.assertEqual(self.closed,1)
    def test_commit_keeps_measured_pose(self):
        m=self.mirror();m.tick();m.finish(True)
        self.assertEqual(self.target.pose.translation.x,10)
    def test_commit_flushes_final_pose_inside_rate_limit(self):
        m=self.mirror();m.tick();self.runtime.pose=pose(11)
        m.last=float('inf')
        self.assertFalse(m.tick())
        m.finish(True)
        self.assertEqual(self.target.pose.translation.x,11)
        self.assertEqual(self.snapshots,2);self.assertEqual(self.closed,1)
    def test_commit_validation_failure_aborts(self):
        m=self.mirror();m.tick();self.runtime.pose=pose(float('nan'))
        with self.assertRaisesRegex(RuntimeError,'Nonfinite'):m.finish(True)
        self.assertTrue(m.closed);self.assertEqual(self.target.pose.translation.x,0)
        self.assertEqual(self.closed,1)
    def test_foreign_edit_is_not_overwritten(self):
        m=self.mirror();m.tick();self.target.pose=pose(99);m.last=-1e20
        with self.assertRaisesRegex(RuntimeError,'Concurrent'):m.tick()
        self.assertTrue(m.closed);self.assertEqual(self.target.pose.translation.x,99)
    def test_watchdog_restores_and_closes(self):
        m=self.mirror();m.tick();m.deadline=0;m._watchdog(0)
        self.assertTrue(m.closed);self.assertEqual(self.target.pose.translation.x,0)
    def test_cached_editor_world_survives_sie_subsystem_none(self):
        self.sub.get_editor_world=lambda:None
        m=self.mod.RuntimeMirror(self.game,['arm'],editor_world=self.editor)
        m.tick();m.finish(False)
        self.assertEqual(self.target.pose.translation.x,0)
    def test_missing_cached_editor_world_rejected(self):
        self.sub.get_editor_world=lambda:None
        with self.assertRaisesRegex(ValueError,'Cache a valid'):self.mirror()
        self.assertEqual(self.closed,0)
    def test_different_editor_world_aborts(self):
        m=self.mirror();m.tick()
        self.sub.get_editor_world=lambda:object()
        with self.assertRaisesRegex(RuntimeError,'world changed'):m.tick()
        self.assertEqual(self.target.pose.translation.x,0)
    def test_wrong_world_and_duplicate_labels_rejected(self):
        with self.assertRaises(ValueError):self.mod.RuntimeMirror(object(),['arm'])
        with self.assertRaises(ValueError):self.mod.RuntimeMirror(self.game,['arm','arm'])
        self.assertEqual(self.closed,0)
    def test_nonfinite_measurement_restores(self):
        m=self.mirror();m.tick();self.runtime.pose=pose(float('nan'));m.last=-1e20
        with self.assertRaisesRegex(RuntimeError,'Nonfinite'):m.tick()
        self.assertEqual(self.target.pose.translation.x,0)

if __name__=='__main__':unittest.main()
