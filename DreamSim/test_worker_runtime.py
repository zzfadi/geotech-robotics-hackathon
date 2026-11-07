"""Worker boundary regressions without Dream SDK, Unreal, network, or media tools."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch


HERE = Path(__file__).resolve().parent


def load_worker():
    modules = {
        'dream': types.SimpleNamespace(DreamClient=Mock()),
        'dream.errors': types.SimpleNamespace(DreamSessionExpired=type('Expired',(Exception,),{}),DreamServerError=type('ServerError',(Exception,),{})),
        'imageio_ffmpeg': types.SimpleNamespace(get_ffmpeg_exe=Mock()),
        'model': types.SimpleNamespace(prepare=Mock()),
        'probe': types.SimpleNamespace(configure=Mock()),
    }
    with patch.dict(sys.modules,modules), patch.object(sys,'path',[str(HERE),*sys.path]):
        spec=importlib.util.spec_from_file_location('worker_runtime_test_subject',HERE/'worker.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class WorkerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.module=load_worker()
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.w=self.module.Worker.__new__(self.module.Worker)
        w=self.w
        w.root=self.root;w.lock=threading.Lock();w.client=Mock()
        w.args=types.SimpleNamespace(runtime_physics=True,cycles=0,checkpoint='auto',grasp_assist=False,pad_contacts=False,initial_state=self.root/'initial.json')
        w.checkpoint=self.module.FLASH;w.arm=None
        w.command={'id':'old'};w.active_request_id=None;w.last=None;w.closed=False
        w.recording=None;w.recorded=0;w.error=None;w.prompt='';w.expected_scope=None;w.episode_started=0
        self.state={'q_left':[0]*6,'q_right':[0]*6,'gap_left_m':.04,'gap_right_m':.04}
        w.args.initial_state.write_text(json.dumps(self.state))
        (self.root/'cameras').mkdir()
        self.calls=[]
        w.api=lambda path,data=None:self.calls.append((path,data))
        w.ue=lambda tool,args:self.calls.append((tool,args))
        w.python=Mock();w.wait_runtime=Mock()
        w.finish_runtime=lambda commit:self.calls.append(('runtime.finish',commit))
        self.command={'id':'current','prompt':'pick up red cube'}

    def test_full_capacity_falls_back_with_fresh_packet(self):
        w=self.w;w.checkpoint=self.module.FULL
        error=self.module.DreamServerError('capacity');error.code='DA-2000';error.reason='pool_full'
        w.client.get_action.side_effect=[error,{'ok':True}]
        packets=[{'sequence':1},{'sequence':2}]
        w.fresh=Mock(side_effect=packets)
        w.select_model=lambda model:setattr(w,'checkpoint',model)
        budget=self.module.ModelBudget()
        action,packet=w.infer(budget,8,'runtime')
        self.assertEqual(packet,packets[1]);self.assertEqual(action,{'ok':True})
        self.assertEqual(budget.full_calls,1);self.assertTrue(budget.full_unavailable)
        self.assertEqual(w.checkpoint,self.module.FLASH)
        self.assertEqual(w.client.get_action.call_count,2)

    def test_expiry_cannot_exceed_full_budget(self):
        w=self.w;w.checkpoint=self.module.FULL
        w.client.get_action.side_effect=[self.module.DreamSessionExpired('expired'),{'ok':True}]
        w.fresh=Mock(side_effect=[{'sequence':1},{'sequence':2},{'sequence':3}])
        w.select_model=lambda model:setattr(w,'checkpoint',model)
        budget=self.module.ModelBudget(full_limit=1)
        action,packet=w.infer(budget,8,'runtime')
        self.assertEqual(budget.full_calls,1);self.assertEqual(w.checkpoint,self.module.FLASH)
        self.assertEqual(packet,{'sequence':3})
        self.assertEqual(w.client.get_action.call_count,2)

    def done(self):
        return [data for path,data in self.calls if path=='/api/worker/done']

    def test_success_terminal_status_follows_runtime_and_shared_task_cleanup(self):
        self.w.run_job(self.command)
        names=[path for path,data in self.calls]
        self.assertLess(names.index('runtime.finish'),names.index('end_task'))
        self.assertLess(names.index('end_task'),names.index('/api/worker/done'))
        self.assertEqual(self.done(),[{'request_id':'current','status':'done'}])
        self.assertIsNone(self.w.active_request_id)

    def test_failed_start_cleans_before_done_and_invalidates_seed_even_when_stop_fails(self):
        playback=self.root/'playback.json';playback.write_text(json.dumps(self.state))
        self.w.wait_runtime.side_effect=RuntimeError('startup rejected')
        def failed_stop(commit):
            self.calls.append(('runtime.finish',commit))
            # Simulate poll changing its latest command while teardown is in flight.
            self.w.command={'id':'different'}
            raise RuntimeError('cleanup transport lost')
        self.w.finish_runtime=failed_stop
        self.w.run_job(self.command)
        self.assertFalse(playback.exists())
        names=[path for path,data in self.calls]
        self.assertLess(names.index('runtime.finish'),names.index('end_task'))
        self.assertLess(names.index('end_task'),names.index('/api/worker/done'))
        self.assertEqual(self.done(),[{'request_id':'current','status':'failed'}])
        events=[data for path,data in self.calls if path=='/api/events']
        self.assertTrue(events)
        self.assertTrue(all(event['request_id']=='current' for event in events))

    def test_end_task_failure_cannot_report_success(self):
        def ue(tool,args):
            self.calls.append((tool,args))
            if tool=='end_task':raise RuntimeError('shared task still open')
        self.w.ue=ue
        self.w.run_job(self.command)
        self.assertEqual(self.done()[0]['status'],'failed')

    def packet(self,scope='runtime',captured=100.5):
        return {'sequence':1,'captured_unix':captured,'captured_monotonic':5,
                'scope':scope,'state':self.state,'frames':{'video.overhead':'','video.wrist_l':'','video.wrist_r':''}}

    def pump_once(self,packet):
        self.w.closed=False
        (self.root/'cameras/observation.json').write_text(json.dumps(packet))
        clock=types.SimpleNamespace(time=lambda:101,sleep=lambda _:setattr(self.w,'closed',True))
        with patch.object(self.module,'time',clock):self.w.pump()

    def test_pump_excludes_editor_and_previous_episode_packets_from_model(self):
        self.w.prompt='pick up red cube';self.w.expected_scope='runtime';self.w.episode_started=100
        for packet in (self.packet(scope='editor'),self.packet(captured=99.5)):
            with self.subTest(packet=packet):
                self.pump_once(packet)
                self.w.client.set_observation.assert_not_called()
        self.pump_once(self.packet())
        self.w.client.set_observation.assert_called_once()
        self.assertEqual(self.w.client.set_observation.call_args.args[1]['prompt'],'pick up red cube')

    def test_fresh_rejects_wrong_scope_and_prior_epoch_even_when_recent(self):
        self.w.episode_started=100
        for packet in (self.packet(scope='editor'),self.packet(captured=99.5)):
            self.w.last=packet
            times=iter((0,0,13))
            clock=types.SimpleNamespace(monotonic=lambda:next(times),time=lambda:101,sleep=lambda _:None)
            with self.subTest(packet=packet),patch.object(self.module,'time',clock):
                with self.assertRaises(TimeoutError):self.w.fresh(scope='runtime')
        self.w.last=self.packet()
        clock=types.SimpleNamespace(monotonic=lambda:0,time=lambda:101)
        with patch.object(self.module,'time',clock):
            self.assertIs(self.w.fresh(scope='runtime'),self.w.last)


if __name__=='__main__':unittest.main()
