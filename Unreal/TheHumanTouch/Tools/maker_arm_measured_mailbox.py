"""Explicit, local-only Table10 measured-feedback mailbox watcher.

No SIE startup, network/hardware calls, editor-world writes or transactions.
The owning game-thread controller supplies the current game world, pinned
session nonce and serial/calibration registration from its trusted registry.
"""
import json
import math
import re
import time
from pathlib import Path
from maker_arm_measured_pose import RuntimeReceiver

MAX_BYTES = 16384
_active = None


def _fresh(stamp, now):
    return type(stamp) in (int,float) and math.isfinite(stamp) and 0 <= now-stamp <= .25


def validate_envelope(value, expected_session, now):
    if type(now) not in (int,float) or not math.isfinite(now):raise ValueError('Invalid local clock')
    if not isinstance(value,dict) or value.get('session_id')!=expected_session:raise ValueError('Mailbox session mismatch')
    if not _fresh(value.get('received_unix'),now):raise ValueError('Stale or future mailbox envelope')
    status=value.get('status');packet=value.get('packet')
    if status=='unavailable':
        if packet is not None:raise ValueError('Unavailable envelope must not contain a pose')
        return None
    if status!='fresh' or not isinstance(packet,dict):raise ValueError('Invalid mailbox status/packet')
    # Check packet age before duplicate suppression: refreshed envelope timestamps
    # cannot make an old hardware sample appear fresh.
    if not _fresh(packet.get('feedback_unix'),now):raise ValueError('Stale or future measured packet')
    if packet.get('side') not in ('left','right'):raise ValueError('Invalid measured side')
    return packet


class MailboxWatcher:
    def __init__(self, path, world, registrations, session_id, hz=10):
        import unreal
        if not isinstance(session_id,str) or re.fullmatch(r'[0-9a-f]{32}',session_id) is None:raise ValueError('Pinned session nonce must be32 lowercase hexadecimal characters')
        if type(hz) not in (int,float) or not 0<hz<=10:raise ValueError('Mailbox rate must be0..10Hz')
        self.engine=unreal;self.path=Path(path).expanduser().resolve();self.session_id=session_id
        self.interval=1/hz;self.last_tick=None;self.last_packets={};self.handle=None;self.closed=False
        # RuntimeReceiver rejects editor worlds and validates exact actor bindings.
        self.receiver=RuntimeReceiver(registrations,world,hz=hz)
        self.state={'running':True,'status':'unavailable','reason':'awaiting fresh mailbox',
                    'applied_samples':0,'duplicate_samples':0,'physical_output_enabled':False}
        try:self.handle=unreal.register_slate_post_tick_callback(self.tick)
        except Exception:
            self.receiver.close();self.closed=True;raise

    def _unavailable(self, reason, now):
        self.state.update(status='unavailable',reason=str(reason)[:240],updated_unix=now)
        self.state.pop('last_pose_result',None)

    def tick(self, delta):
        if self.closed:return
        now=time.time();mono=time.monotonic()
        try:self.receiver._check_world()
        except Exception as error:
            self._unavailable('Runtime ended: '+str(error),now);self.stop();return
        if self.last_tick is not None and mono-self.last_tick+1e-9<self.interval:return
        self.last_tick=mono
        try:
            with self.path.open('rb') as stream:raw=stream.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES:raise ValueError('Mailbox exceeds size limit')
            envelope=json.loads(raw)
            packet=validate_envelope(envelope,self.session_id,now)
            self.state.update(received_unix=envelope['received_unix'],updated_unix=now)
            if packet is None:
                self._unavailable(envelope.get('reason') or 'feedback unavailable',now);return
            fingerprint=json.dumps(packet,sort_keys=True,separators=(',',':'),allow_nan=False)
            side=packet['side']
            if self.last_packets.get(side)==fingerprint:
                self.state.update(status='fresh',reason='duplicate sample suppressed',last_feedback_unix=packet['feedback_unix'])
                self.state['duplicate_samples']+=1;return
            result=self.receiver.apply(packet,now=now,monotonic=mono)
            if not result.get('applied'):
                self._unavailable(result.get('reason','receiver did not apply'),now);return
            self.last_packets[side]=fingerprint
            self.state.update(status='fresh',reason=None,last_feedback_unix=packet['feedback_unix'],last_pose_result=result)
            self.state['applied_samples']+=1
        except (OSError,ValueError,TypeError) as error:
            self._unavailable(error,now)
        except Exception as error:
            self._unavailable('Receiver stopped: '+str(error),now);self.stop()

    def stop(self):
        if self.closed:return
        self.closed=True;handle=self.handle;self.handle=None
        try:
            if handle is not None:self.engine.unregister_slate_post_tick_callback(handle)
        finally:
            self.receiver.close();self.state.update(running=False,status='unavailable');self.state.pop('last_pose_result',None)
            if not self.state.get('reason'):self.state['reason']='watcher stopped'

    def status(self):
        result=dict(self.state)
        if result.get('status')=='fresh':
            now=time.time()
            if not _fresh(result.get('received_unix'),now) or not _fresh(result.get('last_feedback_unix'),now):
                result.update(status='unavailable',reason='last measured sample expired');result.pop('last_pose_result',None)
        if 'last_pose_result' in result:result['last_pose_result']=dict(result['last_pose_result'])
        return result


def start(path, world, registrations, session_id, hz=10):
    global _active
    if _active is not None and not _active.closed:raise RuntimeError('A measured mailbox watcher is already active')
    _active=MailboxWatcher(path,world,registrations,session_id,hz)
    return _active.status()


def stop():
    if _active is not None:_active.stop()


def status():
    return _active.status() if _active is not None else {'running':False,'status':'unavailable','reason':'not started','physical_output_enabled':False}
