"""Write one control-center session (geotech.telemetry/1) per evaluation attempt.

Uses control_center.telemetry.Writer unchanged. Timestamps are the session's
wall-clock start plus bench (simulated) time, so the control center replays the
attempt at bench speed. component is 'edge-model'; the schema's purpose enum
has no 'bench', so sessions say purpose 'sim' and carry 'kinematic bench, not
physics' in their note. Decisions use the schema's 'edge' (edge model), 'loop',
'safety', 'assist', 'operator' and 'controller' names.
"""
import re
import time

from control_center.telemetry import Writer

ROBOT = 'Maker Arm, left (bench)'
SPEED_LIMIT = 0.85
STATE_EVERY = 5            # bench steps (1 Hz at 5 Hz)


def session_id(scenario, variant, seed, table, stamp):
    raw = 'edge-%s-%s-s%d-t%d-%s' % (scenario, variant, seed, table, stamp)
    return re.sub(r'[^A-Za-z0-9._-]', '-', raw)[:80]


class SessionLog:
    def __init__(self, root, scenario, variant, seed, table, stamp=None, limits=None, note=None):
        self.base = time.time()
        stamp = stamp or time.strftime('%Y%m%d-%H%M%S')
        self.writer = Writer(root, session=session_id(scenario, variant, seed, table, stamp), component='edge-model')
        self.session = self.writer.session
        self.dir = self.writer.dir
        self.t = 0.0
        self._event_index = 0
        self.task = 'Grasp and hold the cup'
        self.emit('session', phase='start', robot=ROBOT, purpose='sim',
                  station='Bench table %d, seed %d' % (table, seed),
                  limits=dict({'speed_limit_m_s': SPEED_LIMIT}, **(limits or {})),
                  note=note or ('Edge-model bench evaluation (%s, %s). Kinematic bench, not physics; '
                                'not a physical result.' % (scenario, variant)))

    def emit(self, kind, t=None, **fields):
        if t is not None:
            self.t = t
        return self.writer.emit(kind, ts=self.base + self.t, **fields)

    def contacts(self, table):
        """Forward new bench contact events (grasp, release, drop, collision)."""
        for e in table.events[self._event_index:]:
            what = e['kind'] if e['kind'] in ('grasp', 'release', 'drop', 'collision') else None
            if what is None:
                continue
            obj = e.get('cup') or e.get('what')
            detail = ', '.join('%s=%s' % (k, v) for k, v in e.items() if k not in ('kind', 't', 'cup'))
            self.emit('contact', t=e['t'], what=what, object=str(obj), detail=detail or None)
        self._event_index = len(table.events)

    def state(self, table, step):
        if step % STATE_EVERY == 0:
            p, _ = table.arm.tcp(table.q)
            self.emit('state', t=table.t, arm='left', q=[round(float(x), 4) for x in table.q],
                      grip=round(float(table.grip()), 4), tcp=[round(float(x), 4) for x in p])

    def command(self, t, requested, applied, clamped):
        self.emit('command', t=t, arm='left', requested_m_s=round(float(requested), 4),
                  applied_m_s=round(float(applied), 4), limit_m_s=SPEED_LIMIT, clamped=bool(clamped))
        if clamped:
            self.emit('decision', t=t, by='safety',
                      what='Clamped speed from %.2f to %.2f m/s' % (requested, applied),
                      why='The command asked to move faster than the 0.85 m/s limit')

    def finish(self, result, extra_note=None):
        outcome = 'success' if result['success'] else 'failed'
        detail = ('Gate passed at %.1f s' % result['passed_at_s']) if result['success'] else \
            'Gate not passed: %s' % (', '.join(result['reasons']) or 'no hold')
        self.emit('task', phase='end', name=self.task, outcome=outcome, detail=detail)
        self.emit('session', phase='end', robot=ROBOT, purpose='sim',
                  note=extra_note or ('%s; longest hold %.1f s; %d collisions, %d drops'
                                      % (outcome, result['longest_hold_s'], result['collisions'], result['drops'])))
        self.writer.close()
