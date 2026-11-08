"""Left-to-right cup handover driven by Dream subgoals (simulation fleet).

Phases (each Dream call carries a phase prompt; helpers are labelled in the result):
  reach_left (planned) -> grasp_left -> lift_left -> carry_left (to the handover zone)
  -> reach_right (planned) -> grasp_right -> release_left (+ planned retreat) -> hold_right
Success: the target cup is held by the RIGHT gripper only (runtime friction-grip status),
at least `min_rise_cm` above its start, within 15 degrees of upright, for 5 s continuous.
"""
import math
import time

import numpy as np

JAW_MAX_M = .104825
K_LEFT = ' Keep the right arm still.'
K_RIGHT = ' Keep the left arm still.'
PROMPTS = {
    'grasp_left': 'Close the left gripper around the cup and hold it firmly.'+K_LEFT,
    'lift_left': 'Lift the cup up 5 more centimeters.'+K_LEFT,
    'carry_left': 'Move the cup toward the right arm, keeping it upright.'+K_LEFT,
    'grasp_right': 'Close the right gripper around the cup and hold it firmly.'+K_RIGHT,
    'release_left': 'Open the left gripper and let go of the cup.'+K_RIGHT,
    'hold_right': 'Hold the cup still and upright with the right gripper.'+K_RIGHT,
}
KEY = {'left': 'q_l_chunk', 'right': 'q_r_chunk'}


def other(side):
    return 'right' if side == 'left' else 'left'


def hold_rows(state, side, n):
    return [list(state['q_'+side])+[state['gap_'+side+'_m']/JAW_MAX_M] for _ in range(n)]


def planar_toward(action, state, side, goal_xy_m, pitch_deg, base_xy_m, step_cm=1.5, nudge_cm=4., gain=2., max_cm=6.):
    """Carry: apply Dream's horizontal motion projected on the direction to the handover goal
    (never away from it), keep height, keep the cup upright (tool pitch fixed, yaw follows the
    arm's line of sight). Adds a labelled nudge when Dream's own progress is < 0.5 cm."""
    from pregrasp import _fk, solve_pose
    from scipy.spatial.transform import Rotation
    fk = _fk()
    start = fk.pose(np.clip(np.array(state['q_'+side], dtype=float), fk.low+1e-7, fk.high-1e-7))
    here = start[:3, 3].copy()
    to_goal = np.array([goal_xy_m[0]-here[0], goal_xy_m[1]-here[1]])
    distance = float(np.linalg.norm(to_goal))
    if distance < 1e-4:
        return hold_rows(state, side, 2), {'progress_cm': 0., 'nudged': False}
    direction = to_goal/distance
    rows = action[KEY[side]]
    end = fk.pose(np.clip(np.array(rows[-1][:6], dtype=float), fk.low+1e-7, fk.high-1e-7))[:3, 3]
    progress = max(0., float(np.dot(end[:2]-here[:2], direction)))
    nudged = progress < .005
    progress = min(distance, max_cm/100, progress*gain if not nudged else nudge_cm/100)
    steps = max(1, int(math.ceil(progress*100/(step_cm/4))))
    out, previous = [list(state['q_'+side])+[rows[0][6]]], list(state['q_'+side])
    for k in range(1, steps+1):
        point = here.copy()
        point[:2] += direction*progress*k/steps
        yaw = math.atan2(point[1]-base_xy_m[1], point[0]-base_xy_m[0])
        rot = Rotation.from_euler('z', yaw).as_matrix()@Rotation.from_euler('y', -pitch_deg, degrees=True).as_matrix()
        try:
            previous = solve_pose(point, rot, previous, tolerance_m=.003, tolerance_rad=.1)
        except ValueError:
            break
        out.append(list(previous)+[rows[0][6]])
    achieved = progress*(len(out)-1)/steps
    return out, {'progress_cm': round(achieved*100, 2), 'nudged': nudged, 'remaining_cm': round(distance*100, 2)}


class Handover:
    def __init__(self, tr):
        self.tr = tr
        a = tr.v.get('assist') or {}
        self.zone = a.get('handover_zone_cm', [35., 0.])
        self.rise = float(a.get('handover_rise_cm', 10.))
        self.min_rise = float(a.get('handover_min_rise_cm', 6.))
        self.phase = 'reach_left'
        self.latch = {'left': None, 'right': None}
        self.floor = {'left': None, 'right': None}
        self.grasp_cycles = 0
        self.release_cycles = 0
        self.carry_stalls = 0
        self.counts = {'lift_nudges': 0, 'carry_nudges': 0, 'release_nudges': 0}
        self.seq = 0

    # ---- plumbing ---------------------------------------------------------------------
    def play(self, packet, action, label, extra=None):
        from fleet_worker import write_json, read_json
        tr = self.tr
        self.seq += 1
        rows = max(len(action['q_l_chunk']), len(action['q_r_chunk']))
        for key in ('q_l_chunk', 'q_r_chunk'):
            while len(action[key]) < rows:
                action[key].append(list(action[key][-1]))
        report = tr.dir/('action-%02d.json' % self.seq)
        write_json(report, {'state': packet['state'], 'action': action, 'execution_rows': rows, 'phase': self.phase,
                            'controller_source': label, 'handover': dict(extra or {}), 'checkpoint': tr.v['checkpoint']})
        write_json(tr.dir/'command.json', {'seq': self.seq, 'report': str(report)})
        deadline = time.monotonic()+45
        while True:
            if tr.stopped():
                raise RuntimeError('Stopped during motion')
            tr.status()
            playback = read_json(tr.dir/'playback.json')
            if playback and playback.get('seq') == self.seq and playback.get('done'):
                if playback.get('error'):
                    raise RuntimeError('Handover step %d (%s) rejected: %s' % (self.seq, self.phase, playback['error']))
                return playback
            if time.monotonic() > deadline:
                raise TimeoutError('Handover step %d motion did not finish' % self.seq)
            time.sleep(.1)

    def dream(self, packet, side):
        """One Dream call with this phase's prompt; returns the action adapted for `side` only."""
        tr = self.tr
        tr.prompt_override = PROMPTS[self.phase]
        action, used = tr.infer(packet)
        packet = used or packet
        if tr.v['position_ik']:
            from position_ik import adapt
            try:
                action, _ = adapt(action, packet['state'], tr.v['action_rows'] or 12, active_sides=[side], allow_prefix=True)
            except (ValueError, np.linalg.LinAlgError) as error:
                tr.log('%s: policy target unusable (%s); holding' % (self.phase, error))
                action = {KEY[side]: hold_rows(packet['state'], side, 2), KEY[other(side)]: hold_rows(packet['state'], other(side), 2)}
        from fleet_worker import constrain_motion_side
        action = constrain_motion_side(action, packet['state'], side)
        return action, packet

    def cup(self):
        return (self.tr.status() or {}).get('props', {}).get(self.tr.v['target']['label'], {})

    def grip_holders(self):
        return ((self.tr.status() or {}).get('friction_grip') or {}).get('holding') or {}

    # ---- phases -----------------------------------------------------------------------
    def run(self):
        from pregrasp import approach_action, vertical_only, guard_table, BASES
        tr = self.tr
        target = tr.v['target']['label']
        after = time.time()-1
        for _ in range(int(tr.v.get('assist', {}).get('handover_steps', 60))):
            if tr.stopped():
                tr.result['stopped_reason'] = 'fleet stopped'
                return
            packet = tr.fresh(after)
            state = packet['state']
            c = self.cup()
            tr.result['handover_phase'] = self.phase
            if self.phase != 'reach_left' and 0 <= c.get('tilt_degrees', 0) > 60 and c.get('height_above_start_cm', 0) < 2:
                tr.result['stopped_reason'] = 'cup fell over during %s' % self.phase
                return
            if self.phase == 'reach_left':
                action, info = approach_action(state, tr.v)
                playback = self.play(packet, action, 'planned reach (left, no Dream call)', info)
                self.phase = 'grasp_left'
            elif self.phase in ('grasp_left', 'grasp_right'):
                side = 'left' if self.phase == 'grasp_left' else 'right'
                action, packet = self.dream(packet, side)
                still = list(packet['state']['q_'+side])
                g0 = packet['state']['gap_'+side+'_m']/JAW_MAX_M
                g1 = min(min(r[6] for r in action[KEY[side]]), g0)
                if self.grasp_cycles >= 3:
                    g1 = min(g1, .5)  # labelled: Dream kept the jaws open; close gently
                action[KEY[side]] = [still+[g0+(g1-g0)*k/29] for k in range(30)]
                action[KEY[other(side)]] = hold_rows(packet['state'], other(side), 30)
                if self.latch[other(side)] is not None:
                    action[KEY[other(side)]] = [r[:6]+[self.latch[other(side)]] for r in action[KEY[other(side)]]]
                playback = self.play(packet, action, 'Dream grip, arm held still (%s)' % side, {'dream_grip': g1})
                self.grasp_cycles += 1
                observed = playback.get('gap_'+side+'_m', 1.)
                if observed < .095 and observed-g1*JAW_MAX_M > .005 and observed > .055:
                    self.latch[side] = max(observed-.006, 0.)/JAW_MAX_M
                    from pregrasp import tool_height
                    self.floor[side] = tool_height(playback['q_'+side])
                    tr.log('%s: jaws closed on the cup at %.1f cm' % (self.phase, observed*100))
                    self.grasp_cycles = 0
                    self.phase = 'lift_left' if side == 'left' else 'release_left'
                elif self.grasp_cycles >= 6:
                    tr.result['stopped_reason'] = '%s: jaws never closed on the cup' % self.phase
                    return
            elif self.phase == 'lift_left':
                action, packet = self.dream(packet, 'left')
                action, info = vertical_only(action, packet['state'], 'left', floor_z=self.floor['left'], allow_down_cm=0.,
                                             max_step_cm=.15, up_gain=2.5, min_rise_cm=1.)
                if info.get('nudged'):
                    self.counts['lift_nudges'] += 1
                action[KEY['left']] = [r[:6]+[self.latch['left']] for r in action[KEY['left']]]
                action[KEY['right']] = hold_rows(packet['state'], 'right', len(action[KEY['left']]))
                playback = self.play(packet, action, 'Dream lift, vertical only (left)', info)
                from pregrasp import tool_height
                self.floor['left'] = max(self.floor['left'], tool_height(playback['q_left']))
                if self.cup().get('height_above_start_cm', 0) >= self.rise:
                    self.phase = 'carry_left'
            elif self.phase == 'carry_left':
                action, packet = self.dream(packet, 'left')
                goal = [(self.zone[0]-BASES['left'][0])/100, -(self.zone[1]-BASES['left'][1])/100]
                pitch = float(tr.v.get('assist', {}).get('pitch_deg', 45.) if tr.v.get('assist', {}).get('pitch_deg') != 'auto' else 45.)
                rows, info = planar_toward(action, packet['state'], 'left', goal, pitch, (0., 0.))
                if info.get('nudged'):
                    self.counts['carry_nudges'] += 1
                action = {KEY['left']: [r[:6]+[self.latch['left']] for r in rows],
                          KEY['right']: hold_rows(packet['state'], 'right', len(rows))}
                playback = self.play(packet, action, 'Dream carry toward the handover zone (left)', info)
                c = self.cup()
                local = c.get('table_local_cm') or [0, 0, 0]
                self.carry_stalls = self.carry_stalls+1 if info.get('progress_cm', 0) < .5 else 0
                # The zone is a target, not a hard point: once the left arm can no longer make
                # progress within 8 cm of it, hand over from there (the right reach adapts).
                if (math.dist(local[:2], self.zone) < 2.5 or info.get('remaining_cm', 99) < 1.5
                        or (self.carry_stalls >= 2 and info.get('remaining_cm', 99) < 8)):
                    self.phase = 'reach_right'
            elif self.phase == 'reach_right':
                c = self.cup()
                action = info = None
                for pitch in (45., 60., 55., 50., 30.):
                    for offset in (-3., -2.5, -3.5):
                        variant = {**tr.v, 'objective': {**(tr.v.get('objective') or {}), 'side': 'right'},
                                   'target': {**tr.v['target'], 'location_cm': list(c['table_local_cm'])},
                                   'assist': {**(tr.v.get('assist') or {}), 'pitch_deg': pitch, 'grasp_offset_cm': offset, 'gap_m': .1}}
                        try:
                            action, info = approach_action(state, variant)
                            info = {**info, 'pitch_deg': pitch, 'grasp_offset_cm': offset}
                            break
                        except ValueError:
                            continue
                    if action is not None:
                        break
                if action is None:
                    tr.result['stopped_reason'] = 'right arm cannot reach the cup at the handover zone %s' % [round(v, 1) for v in c['table_local_cm']]
                    return
                action[KEY['left']] = [r[:6]+[self.latch['left']] for r in hold_rows(state, 'left', len(action[KEY['right']]))]
                playback = self.play(packet, action, 'planned reach (right, no Dream call)', info)
                self.phase = 'grasp_right'
            elif self.phase == 'release_left':
                action, packet = self.dream(packet, 'left')
                g_dream = max(r[6] for r in action[KEY['left']])
                self.release_cycles += 1
                g_open = g_dream if g_dream > .9 or self.release_cycles < 3 else .95
                if g_open != g_dream:
                    self.counts['release_nudges'] += 1
                still = list(packet['state']['q_left'])
                g0 = packet['state']['gap_left_m']/JAW_MAX_M
                action = {KEY['left']: [still+[g0+(max(g0, g_open)-g0)*k/19] for k in range(20)],
                          KEY['right']: [r[:6]+[self.latch['right']] for r in hold_rows(packet['state'], 'right', 20)]}
                self.latch['left'] = None
                playback = self.play(packet, action, 'Dream release (left), arm held still', {'dream_grip': g_dream})
                if playback.get('gap_left_m', 0) > .09:
                    # Planned retreat of the empty left gripper, back toward its start pose.
                    from fleet_reset import HOME_Q
                    q0 = np.array(packet['state']['q_left']); q1 = np.array(HOME_Q)
                    rows = [list(q0*(1-s)+q1*s)+[.95] for s in (.5-.5*np.cos(np.pi*np.linspace(0, 1, 30)))]
                    packet = tr.fresh(time.time())
                    action = {KEY['left']: rows, KEY['right']: [r[:6]+[self.latch['right']] for r in hold_rows(packet['state'], 'right', 30)]}
                    action[KEY['left']][0] = list(packet['state']['q_left'])+[packet['state']['gap_left_m']/JAW_MAX_M]
                    playback = self.play(packet, action, 'planned retreat (left, no Dream call)')
                    self.phase = 'hold_right'
            elif self.phase == 'hold_right':
                action, packet = self.dream(packet, 'right')
                action, info = vertical_only(action, packet['state'], 'right', floor_z=self.floor['right'], allow_down_cm=0.,
                                             max_step_cm=.15, up_gain=1.)
                action[KEY['right']] = [r[:6]+[self.latch['right']] for r in action[KEY['right']]]
                action[KEY['left']] = hold_rows(packet['state'], 'left', len(action[KEY['right']]))
                playback = self.play(packet, action, 'Dream hold (right), vertical only', info)
                held = self.verify()
                tr.result['handover_hold'] = held
                if held['passed']:
                    tr.result['stopped_reason'] = 'handover complete: right gripper held the cup %.1f cm up for %.1f s' % (held['min_height_cm'], held['held_s'])
                    tr.log(tr.result['stopped_reason'])
                    return
            after = playback.get('finished_unix') or time.time()
            tr.result['cycles_run'] = self.seq
            tr.result['handover_counts'] = dict(self.counts)
        tr.result['stopped_reason'] = 'handover steps exhausted in %s' % self.phase

    def verify(self, hold_s=5.):
        tr = self.tr
        start, best, heights = None, 0., []
        until = time.monotonic()+hold_s+.8
        while time.monotonic() < until:
            status = tr.status() or {}
            p = status.get('props', {}).get(tr.v['target']['label'], {})
            holders = (status.get('friction_grip') or {}).get('holding') or {}
            ok = (holders.get('right') == tr.v['target']['label'] and holders.get('left') != tr.v['target']['label']
                  and p.get('height_above_start_cm', 0) >= self.min_rise and 0 <= p.get('tilt_degrees', 180) <= 15
                  and p.get('physics_simulated') is True and p.get('gravity_enabled') is True)
            now = time.monotonic()
            start = (start or now) if ok else None
            best = max(best, now-start) if start else best
            heights.append(p.get('height_above_start_cm', 0))
            if best >= hold_s:
                break
            time.sleep(.1)
        return {'passed': best >= hold_s, 'held_s': round(best, 2), 'min_height_cm': round(min(heights or [0]), 2),
                'holder': 'right', 'scope': 'right gripper only (friction-grip model), upright, gravity on'}
