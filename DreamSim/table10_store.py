"""Table 10 controls (web page / VR -> Mac table10_agent -> editor link and real-arm service).

Test motions (move_up, idle, stop) move Table 10; the real arms follow only in drive. Drive is
a request with an expiry (DRIVE_S): the agent turns it into a heartbeat for the headless
real-arm service, which drops back to mirror (torque off) if the heartbeat stops. The server
refuses drive_on from public (Funnel) visitors; drive_off and release are always accepted.
"""
import time
import uuid

ACTIONS = ('move_up', 'idle', 'stop')
DRIVE_ACTIONS = ('drive_on', 'drive_off', 'release')
DRIVE_S = 900.
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
        return [_clip(v, depth+1) for v in value[:16]]
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
        self.drive = {'on': False, 'seq': 0, 'since': None, 'until': None, 'source': None}
        self.release_seq = 0

    def drive_state(self):
        if self.drive['on'] and self.clock() > self.drive['until']:
            self.drive.update(on=False, ended='expired after %d min' % (DRIVE_S/60))
        return dict(self.drive)

    def online(self):
        return self.clock()-self.agent_at < AGENT_FRESH_S

    def request(self, data):
        action = (data or {}).get('action')
        if action in DRIVE_ACTIONS:
            return self.drive_request(action, (data or {}).get('source', 'web'), (data or {}).get('sides'))
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

    def drive_request(self, action, source, sides=None):
        if action == 'drive_off':
            self.drive.update(on=False, ended='switched off from ' + str(source)[:20])
        elif action == 'release':
            self.release_seq += 1
        else:
            service = (self.agent or {}).get('service') or {}
            if not self.online() or not service.get('running'):
                raise ValueError('Real-arm service is not running on the Mac')
            sides = [s for s in ('left', 'right') if s in (sides or ('left', 'right'))]
            if not sides:
                raise ValueError('Choose at least one arm')
            now = self.clock()
            self.drive.update(on=True, seq=self.drive['seq']+1, since=now, until=now+DRIVE_S,
                              source=str(source)[:20], ended=None, sides=sides)
        return {'drive': self.drive_state(), 'release_seq': self.release_seq}

    def agent_update(self, data):
        data = data or {}
        self.agent = _clip({k: data.get(k) for k in ('link', 'bridge', 'sync_deg', 'camera', 'service', 'log', 'view')})
        self.agent_at = self.clock()
        command = self.command
        if command:
            test = (data.get('link') or {}).get('test') or {}
            if test.get('id') == command['id'] and test.get('state'):
                command.update(status=str(test['state'])[:20], detail=str(test.get('detail') or '')[:300],
                               progress=test.get('progress') if isinstance(test.get('progress'), (int, float)) else command['progress'],
                               lag_deg=test.get('lag_deg') if isinstance(test.get('lag_deg'), (int, float)) else None,
                               source=str(test.get('source') or '')[:40] or None, updated_at=self.clock())
            elif data.get('claimed') == command['id'] and command['status'] == 'queued':
                command.update(status='sent', updated_at=self.clock())
            elif command['status'] in ACTIVE and self.clock()-command['created_at'] > 60:
                command.update(status='error', detail='no result from the editor link within 60 s')
        pending = command if command and command['status'] == 'queued' else None
        return {'command': dict(pending) if pending else None, 'drive': self.drive_state(), 'release_seq': self.release_seq}

    def public(self):
        return {'command': dict(self.command) if self.command else None, 'agent': self.agent,
                'agent_online': self.online(), 'drive': self.drive_state(), 'server_time': self.clock()}
