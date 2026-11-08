import copy,json,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
import maker_arm_measured_mailbox as mailbox


class MailboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'pose.json';self.clock=1000.;self.mono=0.;self.callbacks={};self.unregistered=[];self.watchers=[]
        self.receiver=types.SimpleNamespace(calls=[],closed=False,alive=True)
        def check():
            if not self.receiver.alive:raise RuntimeError('no current game world')
        def apply(packet,**clocks):
            self.receiver.calls.append(copy.deepcopy(packet));return {'applied':True,'physical_output_enabled':False,'jaw_measured':False}
        self.receiver._check_world=check;self.receiver.apply=apply;self.receiver.close=lambda:setattr(self.receiver,'closed',True)
        def factory(registrations,world,hz):
            if world!='SIE':raise RuntimeError('editor world rejected')
            return self.receiver
        def register(fn):self.callbacks[1]=fn;return 1
        def unregister(handle):self.unregistered.append(handle);self.callbacks.pop(handle)
        engine=types.SimpleNamespace(register_slate_post_tick_callback=register,unregister_slate_post_tick_callback=unregister)
        self.patches=[patch.dict(sys.modules,{'unreal':engine}),patch.object(mailbox,'RuntimeReceiver',factory),patch.object(mailbox.time,'time',lambda:self.clock),patch.object(mailbox.time,'monotonic',lambda:self.mono)]
        for p in self.patches:p.start()
    def tearDown(self):
        for w in self.watchers:w.stop()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def watcher(self):
        w=mailbox.MailboxWatcher(self.path,'SIE',{'left':{'serial':'test','calibration_id':'test-cal'}},'a'*32);self.watchers.append(w);return w
    def envelope(self,**changes):
        e={'session_id':'a'*32,'status':'fresh','received_unix':self.clock,'packet':{'side':'left','feedback_unix':self.clock,'q_urdf_rad':[0]*6},'reason':None};e.update(changes);return e
    def write(self,e):self.path.write_text(json.dumps(e))
    def advance(self,dt=.1):self.clock+=dt;self.mono+=dt
    def test_explicit_start_current_runtime_and_idempotent_stop(self):
        self.assertEqual(self.callbacks,{})
        with self.assertRaises(RuntimeError):mailbox.MailboxWatcher(self.path,'EDITOR',{},'a'*32)
        self.assertEqual(self.callbacks,{})
        w=self.watcher();self.assertEqual(len(self.callbacks),1);w.stop();w.stop()
        self.assertEqual(self.unregistered,[1]);self.assertTrue(self.receiver.closed)
    def test_nonce_requires_exact_lowercase_hex(self):
        for nonce in ('',None,'A'*32,'a'*31,'g'*32,'a'*33):
            with self.subTest(nonce=nonce),self.assertRaises(ValueError):
                mailbox.MailboxWatcher(self.path,'SIE',{},nonce)
        self.assertEqual(self.callbacks,{})
    def test_status_expires_even_without_another_tick(self):
        w=self.watcher();self.write(self.envelope());w.tick(0)
        self.advance(.251);self.assertEqual(w.status()['status'],'unavailable')
        self.assertNotIn('last_pose_result',w.status())
    def test_fresh_duplicate_rate_and_stale_packet_under_fresh_envelope(self):
        w=self.watcher();e=self.envelope();self.write(e);w.tick(0)
        self.advance(.05);w.tick(0);self.assertEqual(len(self.receiver.calls),1)
        self.advance(.05);w.tick(0);self.assertEqual(w.status()['duplicate_samples'],1)
        self.advance(.2);e['received_unix']=self.clock;self.write(e);w.tick(0)
        self.assertEqual(len(self.receiver.calls),1);self.assertEqual(w.status()['status'],'unavailable')
        self.assertIn('Stale',w.status()['reason'])
    def test_unavailable_missing_and_malformed_never_apply(self):
        w=self.watcher();w.tick(0)
        self.advance();self.write(self.envelope(status='unavailable',packet=None,reason='hardware offline'));w.tick(0)
        self.assertEqual(w.status()['reason'],'hardware offline')
        self.advance();self.path.write_text('{broken');w.tick(0)
        self.assertEqual(self.receiver.calls,[]);self.assertFalse(w.closed)
        self.advance();self.write(self.envelope());w.tick(0);self.assertEqual(len(self.receiver.calls),1)
    def test_session_and_envelope_age_pin(self):
        w=self.watcher()
        for change in ({'session_id':'different'},{'received_unix':999},{'received_unix':1001},{'status':'unavailable'}):
            self.write(self.envelope(**change));w.tick(0);self.advance()
        self.assertEqual(self.receiver.calls,[])
    def test_oversized_file_rejected(self):
        w=self.watcher();self.path.write_bytes(b' '* (mailbox.MAX_BYTES+1));w.tick(0)
        self.assertEqual(self.receiver.calls,[]);self.assertIn('size limit',w.status()['reason'])
    def test_runtime_end_unregisters_without_waiting_for_file(self):
        w=self.watcher();self.receiver.alive=False;w.tick(0)
        self.assertTrue(w.closed);self.assertEqual(self.unregistered,[1]);self.assertEqual(self.receiver.calls,[])
    def test_receiver_validation_failure_does_not_become_fake_pose(self):
        w=self.watcher();self.write(self.envelope())
        def reject(*a,**k):raise ValueError('unregistered measured identity')
        self.receiver.apply=reject;w.tick(0)
        self.assertEqual(w.status()['status'],'unavailable');self.assertEqual(w.status()['applied_samples'],0)
        self.assertEqual(w.last_packets,{})

if __name__=='__main__':unittest.main()
