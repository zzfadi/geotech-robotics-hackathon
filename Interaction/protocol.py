"""Strict, short-lived contact packets. Coordinates are original URDF base RH metres."""
import math
import re

TTL_SECONDS = .1
MAX_FORCE_N = 5.
LINKS = {'link_002', 'link_003', 'link_004', 'link_005', 'link_006', 'link_007'}

def vector(value, size, name):
    if not isinstance(value, (list, tuple)) or len(value) != size or not all(type(v) in (int,float) and math.isfinite(v) for v in value):
        raise ValueError(name+' must contain finite numbers')
    return list(map(float, value))

def validate_contact(event, now):
    if not isinstance(event, dict):raise ValueError('Contact must be an object')
    if type(event.get('version')) is not int or event['version'] != 1 or type(event.get('table')) is not int or event['table'] != 10:raise ValueError('Expected version1 Table10')
    if event.get('side') not in ('left','right'):raise ValueError('Arm side required')
    if event.get('frame') != 'urdf_base_rh':raise ValueError('Unregistered contact coordinate frame')
    if not isinstance(event.get('session_id'),str) or not re.fullmatch(r'[0-9a-f]{32}',event['session_id']):raise ValueError('Session nonce required')
    if not isinstance(event.get('source_id'),str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}',event['source_id']):raise ValueError('Invalid source identity')
    if event.get('source_kind') not in ('hand','object'):raise ValueError('Invalid source kind')
    if type(event.get('seq')) is not int or event['seq'] < 0:raise ValueError('Invalid sequence')
    if event.get('link') not in LINKS:raise ValueError('Unknown or unsupported contact link')
    stamp = event.get('captured_unix')
    if type(stamp) not in (int,float) or not math.isfinite(stamp) or not math.isfinite(now) or not 0 <= now-stamp <= TTL_SECONDS:
        raise ValueError('Contact timestamp expired or future-dated')
    point = vector(event.get('point_m'),3,'point_m');force=vector(event.get('force_n'),3,'force_n')
    if math.sqrt(sum(x*x for x in force)) > MAX_FORCE_N:raise ValueError('Virtual force exceeds5N bound')
    if math.sqrt(sum(x*x for x in point)) > 1.5:raise ValueError('Contact outside bounded base workspace')
    return {**event, 'point_m':point,'force_n':force}

class ContactLease:
    """A new nonce requires local registration; old sessions cannot resurrect force."""
    def __init__(self, session_id, side):
        if not re.fullmatch(r'[0-9a-f]{32}',session_id):raise ValueError('Invalid registered nonce')
        if side not in ('left','right'):raise ValueError('Arm side required')
        self.side=side
        self.session_id=session_id;self.sequences={};self.latest=None
    def accept(self,event,now):
        event=validate_contact(event,now)
        if event['session_id'] != self.session_id or event['side'] != self.side:raise ValueError('Unregistered session')
        key=event['source_id']
        if event['seq'] <= self.sequences.get(key,-1):raise ValueError('Replayed or reordered contact')
        self.sequences[key]=event['seq'];self.latest={**event,'received_unix':now}
        return self.latest
    def current(self,now):
        if self.latest is None:return None
        try:validate_contact(self.latest,now)
        except ValueError:self.latest=None;return None
        if not 0 <= now-self.latest['received_unix'] <= TTL_SECONDS:self.latest=None
        return self.latest
