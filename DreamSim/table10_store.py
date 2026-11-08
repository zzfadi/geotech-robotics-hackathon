"""Table 10 test-motion requests (web page -> Mac table10_agent -> editor real-arm link).

Never enables hardware: the real arms follow only while real_arm_bridge.py is in drive mode,
which the operator switches on at the bridge terminal (with a hand on the E-stop).
"""
import time
import uuid

ACTIONS = ('move_up', 'idle', 'stop')
ACTIVE = ('queued', 'sent', 'running')
AGENT_FRESH_S = 5.
FINAL = ('done', 'rejected', 'aborted', 'stopped', 'error')


def _clip(value, depth=0):
    """Keep agent telemetry small and JSON-plain."""
    if depth > 4:
        return None
    if isinstance(value, dict):
        return {str(k)[:40]: _clip(v, depth+1) for k, v in list(value.items())[:24]}
    if isinstance(value, list):
        return [_clip(v, depth+1) for v in value[:12]]
    if isinstance(value, str):
        return value[:300]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:80]


class Table10Tests:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.command = None
        self.agent = None
        self.agent_at = 0.

    def online(self):
        return self.clock()-self.agent_at < AGENT_FRESH_S

    def request(self, data):
        action = (data or {}).get('action')
        if action not in ACTIONS:
            raise ValueError('Unknown Table 10 test action')
        if not self.online():
            raise ValueError('Table 10 link agent is offline on the Mac')
        busy = self.command and self.command['status'] in ACTIVE
        if busy and action != 'stop':
            raise ValueError('A Table 10 test motion is already running')
        self.command = {'id': uuid.uuid4().hex, 'action': action, 'status': 'queued', 'detail': '',
                        'progress': 0., 'created_at': self.clock()}
        return dict(self.command)

    def agent_update(self, data):
        data = data or {}
        self.agent = _clip({k: data.get(k) for k in ('link', 'bridge', 'sync_deg', 'camera')})
        self.agent_at = self.clock()
        command = self.command
        if command:
            test = (data.get('link') or {}).get('test') or {}
            if test.get('id') == command['id'] and test.get('state'):
                command.update(status=str(test['state'])[:20], detail=str(test.get('detail') or '')[:300],
                               progress=test.get('progress') if isinstance(test.get('progress'), (int, float)) else command['progress'])
            elif data.get('claimed') == command['id'] and command['status'] == 'queued':
                command['status'] = 'sent'
            elif command['status'] in ACTIVE and self.clock()-command['created_at'] > 60:
                command.update(status='error', detail='no result from the editor link within 60 s')
        pending = command if command and command['status'] == 'queued' else None
        return {'command': dict(pending) if pending else None}

    def public(self):
        return {'command': dict(self.command) if self.command else None, 'agent': self.agent,
                'agent_online': self.online(), 'server_time': self.clock()}
