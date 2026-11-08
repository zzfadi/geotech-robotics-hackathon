"""Telemetry contract: schema geotech.telemetry/1.

One session is one directory: events.jsonl (append-only JSON Lines) plus a
frames/ folder for the images the model actually saw. Anything that runs the
arm writes these events from its first run: the Dream loop in the sim, the
Orin Nano arm server, the edge model, a teleop rig. The control center reads
them live (tailing the file) and as history (the same file, later).

Every event carries:
  schema   "geotech.telemetry/1"
  kind     one of KINDS below
  ts       unix seconds (float)
  seq      0, 1, 2 ... within the session
  session  session id (also the directory name)
  source   {"component": "dream-loop" | "arm-server" | "edge-model" | "demo" | ..., "host": str}

Write plain facts plus one human sentence where a person will read it
(decision.what / decision.why). The control center never invents a reason.
"""
import json
import os
import re
import socket
import threading
import time
import uuid

SCHEMA = 'geotech.telemetry/1'

# field -> type (or tuple of types). Optional fields are listed in OPTIONAL.
KINDS = {
    # A session starts and ends once. limits are what the loop enforces.
    'session': {'phase': str, 'robot': str, 'purpose': str},
    # The task a person would name, and the step it is on.
    'task': {'phase': str, 'name': str},
    # One Dream (or other remote model) request: sent, then received / late / failed.
    'network': {'phase': str, 'request_id': str},
    # Hybrid loop mode change: async while replies are on time, sync when late.
    'mode': {'mode': str, 'median_ms': (int, float), 'threshold_ms': (int, float)},
    # How a returned action chunk was played: never skipped ahead.
    'chunk': {'request_id': str, 'actions': int, 'arrived_at_action': int, 'blended': int, 'skipped': int},
    # Commanded arm speed against the clamp.
    'command': {'requested_m_s': (int, float), 'applied_m_s': (int, float), 'limit_m_s': (int, float), 'clamped': bool},
    # Who decided what, and why, in a sentence each.
    'decision': {'by': str, 'what': str, 'why': str},
    # Camera health, including the checks that fail silently.
    'camera': {'role': str, 'ok': bool, 'checks': dict},
    # The exact image sent to the model (path relative to the session directory).
    'frame': {'role': str, 'path': str, 'width': int, 'height': int},
    # Physical events the arm or sim detected.
    'contact': {'what': str},
    # Measured arm state, at a low rate.
    'state': {'q': list, 'grip': (int, float)},
}
OPTIONAL = {
    'session': ('station', 'limits', 'note'),
    'task': ('step', 'step_index', 'steps', 'outcome', 'detail'),
    'network': ('rtt_ms', 'upload_bytes', 'frame_size', 'model', 'error'),
    'mode': ('previous', 'window', 'reason'),
    'chunk': ('waited_ms',),
    'command': ('arm',),
    'decision': ('request_id', 'step', 'routine'),  # routine=True: a step change the Now card already shows
    'camera': ('fps', 'age_ms', 'resolution', 'note'),
    'frame': ('request_id', 'bytes'),
    'contact': ('object', 'detail'),
    'state': ('tcp', 'arm'),
}
ENUMS = {
    ('session', 'phase'): ('start', 'end'),
    ('session', 'purpose'): ('live', 'sim', 'demo', 'replay'),
    ('task', 'phase'): ('start', 'step', 'end'),
    ('task', 'outcome'): ('success', 'failed', 'stopped'),
    ('network', 'phase'): ('sent', 'received', 'late', 'failed'),
    ('mode', 'mode'): ('async', 'sync'),
    ('mode', 'previous'): ('async', 'sync'),
    ('decision', 'by'): ('dream', 'edge', 'operator', 'assist', 'loop', 'safety', 'controller'),
    ('camera', 'role'): ('head', 'wrist_l', 'wrist_r'),
    ('frame', 'role'): ('head', 'wrist_l', 'wrist_r'),
    ('contact', 'what'): ('grasp', 'release', 'drop', 'collision'),
}
# The camera checks that raise no error when they are wrong (Dream workshop).
CAMERA_CHECKS = ('colors', 'orientation', 'role_view', 'present', 'aspect')
CHECK_VALUES = ('ok', 'fail', 'unknown')
COMMON = ('schema', 'kind', 'ts', 'seq', 'session', 'source')
SESSION_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$')


def _is(value, typ):
    types = typ if isinstance(typ, tuple) else (typ,)
    if isinstance(value, bool) and bool not in types:
        return False
    if float in types and isinstance(value, int) and not isinstance(value, bool):
        return True
    return isinstance(value, types)


def validate(event):
    """Problems with one event, as short strings. Empty list means valid."""
    if not isinstance(event, dict):
        return ['not an object']
    problems = ['missing ' + k for k in COMMON if k not in event]
    if event.get('schema') != SCHEMA:
        problems.append('schema must be ' + SCHEMA)
    kind = event.get('kind')
    if kind not in KINDS:
        return problems + ['unknown kind %r' % (kind,)]
    if not _is(event.get('ts'), float):
        problems.append('ts must be a number')
    if not _is(event.get('seq'), int):
        problems.append('seq must be an integer')
    source = event.get('source')
    if not isinstance(source, dict) or not {'component', 'host'} <= set(source):
        problems.append('source needs component and host')
    for field, typ in KINDS[kind].items():
        if field not in event:
            problems.append('%s.%s missing' % (kind, field))
        elif not _is(event[field], typ):
            problems.append('%s.%s has the wrong type' % (kind, field))
    for (k, field), allowed in ENUMS.items():
        if k == kind and field in event and event[field] not in allowed:
            problems.append('%s.%s=%r not in %s' % (kind, field, event[field], '/'.join(allowed)))
    if kind == 'camera' and isinstance(event.get('checks'), dict):
        for name, value in event['checks'].items():
            if name not in CAMERA_CHECKS or value not in CHECK_VALUES:
                problems.append('camera.checks.%s=%r' % (name, value))
    if kind == 'chunk' and event.get('skipped', 0) != 0:
        problems.append('chunk.skipped must be 0: the loop never skips ahead')
    if kind == 'frame' and isinstance(event.get('path'), str):
        if event['path'].startswith('/') or '..' in event['path'].split('/'):
            problems.append('frame.path must stay inside the session directory')
    return problems


def json_schema():
    """Draft-07 JSON Schema of the same contract, for readers in other languages."""
    names = {str: 'string', int: 'integer', float: 'number', bool: 'boolean', list: 'array', dict: 'object'}
    variants = []
    for kind, fields in KINDS.items():
        props = {'kind': {'const': kind}}
        for field, typ in fields.items():
            types = typ if isinstance(typ, tuple) else (typ,)
            props[field] = {'type': sorted({names[t] for t in types})}
            if (kind, field) in ENUMS:
                props[field]['enum'] = list(ENUMS[(kind, field)])
        for field in OPTIONAL.get(kind, ()):
            if (kind, field) in ENUMS:
                props[field] = {'enum': list(ENUMS[(kind, field)])}
        variants.append({'title': kind, 'properties': props, 'required': ['kind'] + list(fields)})
    return {
        '$schema': 'http://json-schema.org/draft-07/schema#',
        '$id': 'https://github.com/zzfadi/geotech-robotics-hackathon/control_center/telemetry.schema.json',
        'title': 'GeoTech robot telemetry event (%s)' % SCHEMA,
        'type': 'object',
        'required': list(COMMON),
        'properties': {
            'schema': {'const': SCHEMA},
            'kind': {'enum': list(KINDS)},
            'ts': {'type': 'number'},
            'seq': {'type': 'integer', 'minimum': 0},
            'session': {'type': 'string', 'pattern': SESSION_ID.pattern},
            'source': {'type': 'object', 'required': ['component', 'host'],
                       'properties': {'component': {'type': 'string'}, 'host': {'type': 'string'}}},
        },
        'oneOf': variants,
    }


def new_session_id(prefix='s'):
    return '%s-%s-%s' % (prefix, time.strftime('%Y%m%d-%H%M%S'), uuid.uuid4().hex[:6])


class Writer:
    """Append-only session writer. Thread-safe; flushes every event so a
    reader tailing the file sees it at once. Validates before writing."""

    def __init__(self, root, session=None, component='unknown', clock=None):
        self.session = session or new_session_id()
        if not SESSION_ID.match(self.session):
            raise ValueError('Session id may use letters, digits, dot, dash and underscore')
        self.dir = os.path.join(root, self.session)
        os.makedirs(os.path.join(self.dir, 'frames'), exist_ok=True)
        self.source = {'component': component, 'host': socket.gethostname()}
        self.clock = clock or time.time
        self.seq = 0
        self._lock = threading.Lock()
        self._fh = open(os.path.join(self.dir, 'events.jsonl'), 'a', encoding='utf-8')

    def emit(self, kind, ts=None, **fields):
        with self._lock:
            event = {'schema': SCHEMA, 'kind': kind, 'ts': round(self.clock() if ts is None else ts, 4),
                     'seq': self.seq, 'session': self.session, 'source': self.source}
            event.update((k, v) for k, v in fields.items() if v is not None)
            problems = validate(event)
            if problems:
                raise ValueError('Invalid %s event: %s' % (kind, '; '.join(problems)))
            self._fh.write(json.dumps(event, separators=(',', ':')) + '\n')
            self._fh.flush()
            self.seq += 1
            return event

    def frame_path(self, name):
        """Absolute path to write a frame to, and the relative path to log."""
        rel = 'frames/' + name
        return os.path.join(self.dir, rel), rel

    def close(self):
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read(path, start=0):
    """Events from byte offset start; returns (events, next_offset). Ignores a
    trailing partial line that a writer is still finishing."""
    events = []
    with open(path, 'rb') as fh:
        fh.seek(start)
        data = fh.read()
    end = data.rfind(b'\n') + 1
    for line in data[:end].splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events, start + end
