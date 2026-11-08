"""Table 10 telemetry relay. Never enables hardware or computes motor commands."""
import math
import re
import secrets
import time
import uuid


def vector(value, size=3):
    if not isinstance(value, list) or len(value) != size or any(
            type(x) not in (int, float) or not math.isfinite(x) for x in value):
        raise ValueError('Expected finite numeric vector')
    return list(value)


class InteractionStore:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        self.tokens = {}
        for role in ('controller', 'producer'):
            key = path / ('interaction-' + role + '-token')
            if not key.exists():
                key.write_text(secrets.token_urlsafe(32))
                key.chmod(0o600)
            self.tokens[role] = key.read_text().strip()
        self.session = None
        self.state = None
        self.state_at = 0.
        self.contact = None
        self.sequences = {}
        self.stop_requested = True

    def authorized(self, role, header):
        return secrets.compare_digest(header or '', 'Bearer ' + self.tokens[role])

    def begin(self, data):
        host = data.get('host')
        if data.get('side') not in ('left','right'):
            raise ValueError('Controller arm side required')
        if not isinstance(host, str) or not 1 <= len(host) <= 128:
            raise ValueError('Controller host identity required')
        if self.state is not None and self.clock() - self.state_at < 1:
            raise ValueError('Current controller lease is active')
        self.session = uuid.uuid4().hex
        self.host = host
        self.side = data['side']
        self.state = None
        self.contact = None
        self.sequences = {}
        # This only allows monitoring delivery. It is never hardware enable.
        self.stop_requested = False
        return {'session_id': self.session, 'table': 10, 'physical_enable_available': False}

    def check_session(self, data):
        if not self.session or data.get('session_id') != self.session:
            raise ValueError('Unknown interaction session')

    def heartbeat(self, data):
        self.check_session(data)
        if data.get('side') != self.side:
            raise ValueError('Wrong controller arm')
        mode = data.get('mode')
        if mode not in ('monitor', 'holding', 'armed', 'fault'):
            raise ValueError('Invalid controller mode')
        if type(data.get('local_deadman')) is not bool:
            raise ValueError('Local deadman state required')
        feedback = data.get('feedback_unix')
        if type(feedback) not in (int, float) or not math.isfinite(feedback) or not 0 <= self.clock()-feedback <= .25:
            raise ValueError('Controller feedback is stale or clock is unsynchronized')
        state = {key: vector(data.get(key), 6) for key in
                 ('q_sdk_rad', 'dq_sdk_rad_s', 'torque_nm', 'temperature_c')}
        faults = data.get('faults')
        if not isinstance(faults, list) or len(faults) != 6 or any(type(v) is not bool for v in faults):
            raise ValueError('Six boolean motor fault states required')
        serial = data.get('serial')
        if not isinstance(serial, str) or not 1 <= len(serial) <= 128:
            raise ValueError('Controller adapter serial required')
        state.update(faults=list(faults), serial=serial, side=self.side)
        calibration = data.get('calibration_id')
        if calibration is not None and (not isinstance(calibration, str) or len(calibration) > 128):
            raise ValueError('Invalid calibration identifier')
        state.update(mode=mode, local_deadman=data['local_deadman'],
                     feedback_unix=feedback, calibration_id=calibration)
        self.state, self.state_at = state, self.clock()
        return {'ok': True, 'stop_requested': self.stop_requested}

    def publish(self, data):
        self.check_session(data)
        if data.get('side') != self.side:
            raise ValueError('Contact targets a different arm')
        if type(data.get('version')) is not int or data['version'] != 1 or type(data.get('table')) is not int or data['table'] != 10 or data.get('frame') != 'urdf_base_rh':
            raise ValueError('Expected Table 10 and URDF base right-handed coordinates')
        if data.get('source_kind') not in ('hand', 'object') or data.get('link') not in ['link_%03d' % i for i in range(2, 8)]:
            raise ValueError('Invalid contact source or link')
        source, seq, captured = data.get('source_id'), data.get('seq'), data.get('captured_unix')
        if not isinstance(source, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}', source) or type(seq) is not int or seq < 0:
            raise ValueError('Invalid source sequence')
        if seq <= self.sequences.get(source, -1):
            raise ValueError('Replayed contact sequence')
        if type(captured) not in (int, float) or not math.isfinite(captured) or not 0 <= self.clock()-captured <= .1:
            raise ValueError('Stale contact or unsynchronized clock')
        point, force = vector(data.get('point_m')), vector(data.get('force_n'))
        if math.sqrt(sum(x*x for x in force)) > 5 or math.sqrt(sum(x*x for x in point)) > 1.5:
            raise ValueError('Contact exceeds relay envelope')
        self.sequences[source] = seq
        self.contact = {key: data[key] for key in ('version', 'session_id', 'side', 'table', 'source_id', 'source_kind', 'seq', 'captured_unix', 'link', 'frame')}
        self.contact.update(point_m=point, force_n=force, received_unix=self.clock())
        return {'ok': True, 'stop_requested': self.stop_requested}

    def stop(self):
        self.stop_requested = True
        self.contact = None
        return {'stop_requested': True, 'physical_estop': False}

    def next_contact(self, session):
        self.check_session({'session_id': session})
        now = self.clock()
        online = self.state is not None and now-self.state_at <= .25 and now-self.state['feedback_unix'] <= .25
        contact = self.contact
        if (self.stop_requested or not online or contact is None or
                now-contact['received_unix'] > .1 or now-contact['captured_unix'] > .1):
            contact = None
        return {'contact': contact, 'stop_requested': self.stop_requested,
                'server_time': now, 'hardware_enable': False}

    def public(self):
        now = self.clock()
        online = self.state is not None and now-self.state_at <= .25 and now-self.state['feedback_unix'] <= .25
        fresh = self.contact is not None and now-self.contact['captured_unix'] <= .1
        return {'table': 10, 'session_id': self.session, 'controller_online': online,
                'host': getattr(self, 'host', None), 'side': getattr(self, 'side', None), 'state': self.state if online else None,
                'contact': self.contact if fresh else None, 'stop_requested': self.stop_requested,
                'physical_motion_verified': False, 'hardware_enable_available': False,
                'message': 'Local SDK controller and validated calibration required; web cannot arm motors.'}
