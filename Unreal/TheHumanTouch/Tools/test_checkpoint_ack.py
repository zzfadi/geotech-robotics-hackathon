import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock,patch

class AcknowledgementTests(unittest.TestCase):
    def setUp(self):
        self.unreal=types.SimpleNamespace(CoPresenceFabLibrary=Mock())
        self.widgets=types.SimpleNamespace(after=Mock())
        copresence=types.ModuleType('copresence')
        copresence.animation=Mock();copresence.tracking=Mock();copresence.ui_widgets=self.widgets
        path=Path(__file__).resolve().parents[1]/'Plugins/CoPresence/Content/Python/copresence/checkpoints.py'
        spec=importlib.util.spec_from_file_location('checkpoint_ack_test',path)
        self.m=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'unreal':self.unreal,'copresence':copresence}): spec.loader.exec_module(self.m)
        self.m.status.update(state='saving',started=self.m.time.time())
        self.info={'task':None,'animation':False,'maps':[],'content':[],'pending':[]}
        self.m.inspect=lambda:self.info

    def test_acknowledged_save_completes(self):
        self.m._verify_save()
        self.assertEqual(self.m.status['state'],'saved')

    def test_pending_retries_never_republish_map(self):
        self.info['pending']=['/Game/Maps/Empty']
        for _ in range(5): self.m._verify_save()
        calls=self.unreal.CoPresenceFabLibrary.persist_session_changes.call_args_list
        self.assertEqual(len(calls),3)
        self.assertTrue(all(c.args==(False,) for c in calls))
        self.assertEqual(self.m.status['state'],'saving')

    def test_dirty_or_active_editor_cannot_complete(self):
        for field,value in [('task','active'),('animation',True),('maps',['map']),('content',['mesh'])]:
            with self.subTest(field=field):
                self.info[field]=value;self.m._verify_save()
                self.assertEqual(self.m.status['state'],'error')
                self.info[field]=[] if field in ('maps','content') else None
        self.unreal.CoPresenceFabLibrary.persist_session_changes.assert_not_called()

    def test_timeout_fails_closed(self):
        self.info['pending']=['map'];self.m.status['started']-=20
        self.m._verify_save();self.assertEqual(self.m.status['state'],'error')

if __name__=='__main__': unittest.main()
