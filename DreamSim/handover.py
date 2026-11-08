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
    'place_right': 'Put the cup down on the table gently, keeping it upright.'+K_RIGHT,
    'release_right': 'Open the right gripper and let go of the cup.'+K_RIGHT,
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
    # Keep the tool's current pitch and roll exactly (re-imposing a nominal orientation each
    # step jostled the held cup); only turn about vertical to follow the arm's line of sight.
    yaw0 = math.atan2(here[1]-base_xy_m[1], here[0]-base_xy_m[0])
    for k in range(1, steps+1):
        point = here.copy()
        point[:2] += direction*progress*k/steps
        yaw = math.atan2(point[1]-base_xy_m[1], point[0]-base_xy_m[0])
        rot = Rotation.from_euler('z', yaw-yaw0).as_matrix()@start[:3, :3]
        try:
            previous = solve_pose(point, rot, previous, tolerance_m=.003, tolerance_rad=.1)
        except ValueError:
            break
        out.append(list(previous)+[rows[0][6]])
    achieved = progress*(len(out)-1)/steps
    return out, {'progress_cm': round(achieved*100, 2), 'nudged': nudged, 'remaining_cm': round(distance*100, 2)}


def vertical_down(action, state, side, step_cm=.15, gain=2., nudge_cm=1.5, max_cm=6.):
    """Place: apply only Dream's downward motion (scaled), cup kept upright; labelled nudge
    when Dream's own descent is < 0.3 cm. Stops at the first unreachable row."""
    from pregrasp import _fk, solve_pose
    fk = _fk()
    start = fk.pose(np.clip(np.array(state['q_'+side], dtype=float), fk.low+1e-7, fk.high-1e-7))
    rows = action[KEY[side]]
    end = fk.pose(np.clip(np.array(rows[-1][:6], dtype=float), fk.low+1e-7, fk.high-1e-7))[2, 3]
    descent = max(0., float(start[2, 3]-end))*gain
    nudged = descent < .003
    descent = min(max_cm/100, nudge_cm/100 if nudged else descent)
    steps = max(1, int(math.ceil(descent*100/step_cm)))
    out, previous = [list(state['q_'+side])+[rows[0][6]]], list(state['q_'+side])
    for k in range(1, steps+1):
        point = start[:3, 3]-np.array([0, 0, descent*k/steps])
        try:
            previous = solve_pose(point, start[:3, :3], previous, tolerance_m=.003, tolerance_rad=.1)
        except ValueError:
            break
        out.append(list(previous)+[rows[0][6]])
    return out, {'descent_cm': round(descent*100*(len(out)-1)/steps, 2), 'nudged': nudged}


def lift_clear_rows(q, up_cm=10., steps=24, gap=.95):
    """Straight up with open jaws, tool orientation kept (stops at the first unreachable row)."""
    from pregrasp import _fk, solve_pose
    fk = _fk()
    q = list(q)
    f = fk.pose(np.clip(q, fk.low+1e-7, fk.high-1e-7))
    rows = [q+[gap]]
    for k in range(1, steps+1):
        try:
            q = solve_pose(f[:3, 3]+np.array([0, 0, up_cm/100*k/steps]), f[:3, :3], q, .003, .1)
        except ValueError:
            break
        rows.append(list(q)+[gap])
    return rows


def back_off_rows(q, back_cm=10., steps=20, gap=.95):
    """Horizontal move toward the arm base along its line of sight, orientation kept: the open
    jaws slide off a cup the same way a side grasp slides on."""
    from pregrasp import _fk, solve_pose
    fk = _fk()
    q = list(q)
    f = fk.pose(np.clip(q, fk.low+1e-7, fk.high-1e-7))
    back = -f[:2, 3]/max(1e-6, float(np.linalg.norm(f[:2, 3])))
    rows = [q+[gap]]
    for k in range(1, steps+1):
        point = f[:3, 3].copy()
        point[:2] += back*back_cm/100*k/steps
        try:
            q = solve_pose(point, f[:3, :3], q, .003, .1)
        except ValueError:
            break
        rows.append(list(q)+[gap])
    return rows


def retreat_rows(q, up_cm=14., back_cm=10., home_rows=45, back_first=False):
    """Empty-gripper retreat that cannot sweep the cup. Default (after placing): straight up
    until the open jaws clear the cup, back toward the base, then a joint move home. back_first
    (after letting go at the handover, the other gripper still holding the cup): slide out
    first, then up, then home."""
    from fleet_reset import HOME_Q
    if back_first:
        rows = back_off_rows(q, back_cm)
        rows += lift_clear_rows(rows[-1][:6], up_cm, steps=28)[1:]
    else:
        rows = lift_clear_rows(q, up_cm, steps=28)
        rows += back_off_rows(rows[-1][:6], back_cm)[1:]
    q0 = np.array(rows[-1][:6]); q1 = np.array(HOME_Q)
    rows += [list(q0*(1-t)+q1*t)+[.95] for t in (.5-.5*np.cos(np.pi*np.linspace(0, 1, home_rows)))[1:]]
    return densify(rows)


def densify(rows, max_step=.05):
    """Insert joint-space rows wherever consecutive rows differ by more than max_step rad
    (near-folded elbow poses make a 5 mm Cartesian step a 0.26 rad joint jump)."""
    out = [rows[0]]
    for row in rows[1:]:
        a, b = np.array(out[-1], dtype=float), np.array(row, dtype=float)
        n = int(np.ceil(np.abs(b[:6]-a[:6]).max()/max_step))
        out += [list(a+(b-a)*k/n) for k in range(1, n)] if n > 1 else []
        out.append(list(row))
    return out


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
        self.lift_ceiling = None
        self.place = a.get('task_place', False)
        self.counts = {'lift_nudges': 0, 'carry_nudges': 0, 'release_nudges': 0, 'place_nudges': 0}
        self.seq = 0
        self.trace = []
        self.events_after = time.time()
        self.place_heights = []
        self.recoveries = 0
        self.right_reaches = 0
        self.left_reaches = 0

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
            self.sample(tr.status() or {})
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

    def sample(self, status, every_s=.25):
        """Compact cup/grip trace (height, tilt, holders) for diagnosing drops; kept in the result."""
        now = time.time()
        if self.trace and now-self.trace[-1]['t'] < every_s:
            return
        p = status.get('props', {}).get(self.tr.v['target']['label'], {})
        grip = status.get('friction_grip') or {}
        self.trace.append({'t': round(now, 2), 'seq': self.seq, 'phase': self.phase, 'h': round(p.get('height_above_start_cm', 0), 2),
                           'xyz': [round(v, 1) for v in p.get('table_local_cm') or []],
                           'tilt': round(p.get('tilt_degrees', 0), 1),
                           'held': ''.join(k[0] for k, v in (grip.get('holding') or {}).items() if v == self.tr.v['target']['label'])})
        events = [e for e in grip.get('events') or [] if e.get('t', 0) > self.events_after]
        if events:
            self.events_after = max(e['t'] for e in events)
            self.trace[-1]['events'] = events
        del self.trace[:-240]
        self.tr.result['handover_trace'] = self.trace

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
            if (self.phase in ('lift_left', 'carry_left') and self.grip_holders().get('left') != target
                    and c.get('height_above_start_cm', 99) < 3 and c.get('tilt_degrees', 90) < 20 and c.get('speed_cm_s', 99) < 3):
                # The left grip lost the cup and it is standing on the table: open, lift clear, re-grasp.
                if self.recoveries >= 2:
                    tr.result['stopped_reason'] = 'left grip lost the cup during %s (after %d re-grasps)' % (self.phase, self.recoveries)
                    return
                self.recoveries += 1
                self.counts['regrasps'] = self.recoveries
                tr.log('%s: left grip lost the cup; re-grasping where it stands (%d)' % (self.phase, self.recoveries))
                q = list(state['q_left'])
                g0 = state['gap_left_m']/JAW_MAX_M
                rows = [q+[g0+(.95-g0)*k/14] for k in range(15)]+lift_clear_rows(q, 10.)[1:]
                action = {KEY['left']: rows, KEY['right']: hold_rows(state, 'right', len(rows))}
                playback = self.play(packet, action, 'planned re-grasp: open and lift clear (left, no Dream call)')
                self.latch['left'] = None
                self.lift_ceiling = None
                self.carry_stalls = 0
                self.phase = 'reach_left'
                after = playback.get('finished_unix') or time.time()
                continue
            if self.phase == 'reach_left':
                variant = tr.v
                if self.recoveries or self.left_reaches:
                    # Re-grasp where the cup now stands (live position, not the reset layout); after
                    # a miss, vary the depth so the cup lands inside the pad corridor.
                    depth = float((tr.v.get('assist') or {}).get('grasp_depth_cm', 0.))+(0., 1., -.5)[self.left_reaches % 3]
                    variant = {**tr.v, 'target': {**tr.v['target'], 'location_cm': list(c['table_local_cm'])},
                               'assist': {**(tr.v.get('assist') or {}), 'grasp_depth_cm': depth}}
                try:
                    action, info = approach_action(state, variant)
                except ValueError:
                    if variant is tr.v:
                        raise
                    variant = {**variant, 'assist': {**variant['assist'], 'grasp_depth_cm': (tr.v.get('assist') or {}).get('grasp_depth_cm', 0.)}}
                    action, info = approach_action(state, variant)
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
                # The right grasp counts only once the friction grip formed, so the left never
                # lets go of a cup the right jaws merely touch.
                gripped = side == 'left' or self.grip_holders().get('right') == target
                if observed < .095 and observed-g1*JAW_MAX_M > .005 and observed > .055 and gripped:
                    self.latch[side] = max(observed-.006, 0.)/JAW_MAX_M
                    from pregrasp import tool_height
                    self.floor[side] = tool_height(playback['q_'+side])
                    tr.log('%s: jaws closed on the cup at %.1f cm' % (self.phase, observed*100))
                    self.grasp_cycles = 0
                    self.phase = 'lift_left' if side == 'left' else 'release_left'
                elif side == 'left' and self.grasp_cycles >= 2 and observed <= .055 and self.left_reaches < 2:
                    # Left jaws closed past the cup on the table (place 8, T00): open, lift clear and
                    # reach again from the live cup position, 1 cm deeper.
                    self.left_reaches += 1
                    self.counts['left_reaches'] = self.left_reaches
                    tr.log('grasp_left: jaws stopped at %.1f cm (not the cup body); re-reaching (%d)' % (observed*100, self.left_reaches))
                    q = list(packet['state']['q_left'])
                    g0 = packet['state']['gap_left_m']/JAW_MAX_M
                    rows = [q+[g0+(.95-g0)*k/14] for k in range(15)]+lift_clear_rows(q, 8.)[1:]
                    action = {KEY['left']: rows, KEY['right']: hold_rows(packet['state'], 'right', len(rows))}
                    playback = self.play(packet, action, 'planned re-reach: open and lift clear (left, no Dream call)')
                    self.grasp_cycles = 0
                    self.phase = 'reach_left'
                elif side == 'right' and self.grasp_cycles >= 2 and observed <= .055 and self.right_reaches < 2:
                    # Jaws closed past the cup or on a narrow off-centre chord (place 6, T05: 5.2 cm):
                    # open, slide back out, and reach again from the live cup position with a
                    # different depth/offset.
                    self.right_reaches += 1
                    self.counts['right_reaches'] = self.right_reaches
                    tr.log('grasp_right: jaws stopped at %.1f cm (not the cup body); re-reaching (%d)' % (observed*100, self.right_reaches))
                    q = list(packet['state']['q_right'])
                    g0 = packet['state']['gap_right_m']/JAW_MAX_M
                    rows = [q+[g0+(.95-g0)*k/14] for k in range(15)]+back_off_rows(q, 8.)[1:]
                    action = {KEY['right']: rows, KEY['left']: [r[:6]+[self.latch['left']] for r in hold_rows(packet['state'], 'left', len(rows))]}
                    playback = self.play(packet, action, 'planned re-reach: open and slide out (right, no Dream call)')
                    self.grasp_cycles = 0
                    self.phase = 'reach_right'
                elif self.grasp_cycles >= 6:
                    tr.result['stopped_reason'] = '%s: jaws never closed on the cup' % self.phase
                    return
            elif self.phase == 'lift_left':
                action, packet = self.dream(packet, 'left')
                if self.lift_ceiling is None:
                    from pregrasp import tool_height
                    # Hand height at which the cup is `rise` cm up (+1 cm), so the right arm can still reach it.
                    self.lift_ceiling = tool_height(packet['state']['q_left'])+(self.rise+1-self.cup().get('height_above_start_cm', 0))/100
                action, info = vertical_only(action, packet['state'], 'left', floor_z=self.floor['left'], allow_down_cm=0.,
                                             max_step_cm=.15, up_gain=2.5, min_rise_cm=1., ceiling_z=self.lift_ceiling)
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
                # Slide in from the side first: descending from above passes the left gripper,
                # which holds the cup from above-left (place 2, T03: the reach knocked it loose).
                # 60 deg first: every 60 deg right grasp closed on the cup (place 2, 6/6); 45 deg
                # closed on nothing 4 times in 16 (place 3-4).
                for mode in ('side', 'above'):
                    for pitch in (60., 55., 45., 50., 30.):
                        for offset in ((-3., -2.5, -3.5), (-2.5, -3., -3.5), (-3.5, -3., -2.5))[self.right_reaches % 3]:
                            variant = {**tr.v, 'objective': {**(tr.v.get('objective') or {}), 'side': 'right'},
                                       'target': {**tr.v['target'], 'location_cm': list(c['table_local_cm'])},
                                       'assist': {**(tr.v.get('assist') or {}), 'pitch_deg': pitch, 'grasp_offset_cm': offset, 'gap_m': .1,
                                                  # Cup axis 2 cm inside the pad corridor (flange -17..-10 cm), not at the
                                                  # fingertip edge: at depth 0 the jaws closed past the cup 4/25 times.
                                                  'grasp_depth_cm': float((tr.v.get('assist') or {}).get('handover_grasp_depth_cm', 2.))
                                                                    + (0., 1., -.5)[self.right_reaches % 3]}}
                            try:
                                action, info = approach_action(state, variant, mode=mode)
                                info = {**info, 'pitch_deg': pitch, 'grasp_offset_cm': offset, 'cup_cm': [round(v, 2) for v in c['table_local_cm']],
                                        'grasp_depth_cm': variant['assist']['grasp_depth_cm']}
                                break
                            except ValueError:
                                continue
                        if action is not None:
                            break
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
                    # Planned retreat of the empty left gripper: slide out, up, then home (a direct
                    # joint move home swept through the cup the right gripper holds; place 3, T03).
                    packet = tr.fresh(time.time())
                    rows = retreat_rows(packet['state']['q_left'], up_cm=8., back_cm=10., back_first=True)
                    action = {KEY['left']: rows, KEY['right']: [r[:6]+[self.latch['right']] for r in hold_rows(packet['state'], 'right', len(rows))]}
                    playback = self.play(packet, action, 'planned retreat (left: out, up, home; no Dream call)')
                    self.phase = 'hold_right'
            elif self.phase == 'hold_right':
                action, packet = self.dream(packet, 'right')
                action, info = vertical_only(action, packet['state'], 'right', floor_z=self.floor['right'], allow_down_cm=0.,
                                             max_step_cm=.15, up_gain=1.)
                action[KEY['right']] = [r[:6]+[self.latch['right']] for r in action[KEY['right']]]
                action[KEY['left']] = hold_rows(packet['state'], 'left', len(action[KEY['right']]))
                playback = self.play(packet, action, 'Dream hold (right), vertical only', info)
                held = self.verify(hold_s=3. if self.place else 5.)
                tr.result['handover_hold'] = held
                if held['passed']:
                    tr.log('handover: right gripper held the cup %.1f cm up for %.1f s' % (held['min_height_cm'], held['held_s']))
                    if not self.place:
                        tr.result['stopped_reason'] = 'handover complete: right gripper held the cup %.1f cm up for %.1f s' % (held['min_height_cm'], held['held_s'])
                        tr.result['goal_text'] = 'Handover complete · right gripper held the cup %.1f cm up for %.1f s' % (held['min_height_cm'], held['held_s'])
                        tr.result['goal_passed'] = True
                        return
                    self.phase = 'place_right'
            elif self.phase == 'place_right':
                action, packet = self.dream(packet, 'right')
                # Never drive the cup into the table: descend at most to just above contact.
                # Stop ~0.8 cm above contact: pressing a held cup into the table stored penetration
                # that popped it over on release (place 4 T03, place 5 T02).
                rows, info = vertical_down(action, packet['state'], 'right', max_cm=max(0., self.cup().get('height_above_start_cm', 6)-.8))
                if info.get('nudged'):
                    self.counts['place_nudges'] = self.counts.get('place_nudges', 0)+1
                action = {KEY['right']: [r[:6]+[self.latch['right']] for r in rows],
                          KEY['left']: hold_rows(packet['state'], 'left', len(rows))}
                from pregrasp import guard_table
                action, _ = guard_table(action, packet['state'], 'right')
                playback = self.play(packet, action, 'Dream place, downward only (right)', info)
                h = self.cup().get('height_above_start_cm', 99)
                self.place_heights.append(h)
                # Resting height varies with the grasp (0.7 cm seen on T04): release once the cup is
                # within 1 cm of its start, or has stopped descending while low (it is on the table).
                resting = len(self.place_heights) >= 2 and self.place_heights[-2]-h < .15 and h < 2.5
                if h <= 1.2 or resting or (len(rows) < 2 and h < 3):
                    self.phase = 'release_right'
            elif self.phase == 'release_right':
                action, packet = self.dream(packet, 'right')
                g_dream = max(r[6] for r in action[KEY['right']])
                self.release_cycles_right = getattr(self, 'release_cycles_right', 0)+1
                g_open = g_dream if g_dream > .9 or self.release_cycles_right < 3 else .95
                if g_open != g_dream:
                    self.counts['release_nudges'] += 1
                still = list(packet['state']['q_right'])
                g0 = packet['state']['gap_right_m']/JAW_MAX_M
                action = {KEY['right']: [still+[g0+(max(g0, g_open)-g0)*k/19] for k in range(20)],
                          KEY['left']: hold_rows(packet['state'], 'left', 20)}
                self.latch['right'] = None
                playback = self.play(packet, action, 'Dream release (right), arm held still', {'dream_grip': g_dream})
                if playback.get('gap_right_m', 0) > .09:
                    packet = tr.fresh(time.time())
                    tr.result['after_release'] = dict(self.trace[-1]) if self.trace else None
                    rows = retreat_rows(packet['state']['q_right'])
                    action = {KEY['right']: rows, KEY['left']: hold_rows(packet['state'], 'left', len(rows))}
                    playback = self.play(packet, action, 'planned retreat (right, up clear of the cup, then home; no Dream call)')
                    placed = self.verify_placed()
                    tr.result['place_check'] = placed
                    tr.result['handover_hold'] = {**placed, 'max_height_cm': placed.get('height_cm')}
                    if placed['passed']:
                        tr.result['stopped_reason'] = 'handover and place complete: cup upright on the table, released, for %.1f s' % placed['held_s']
                        tr.result['goal_text'] = 'Handed over and placed · cup upright on the table for %.1f s' % placed['held_s']
                        tr.result['goal_passed'] = True
                        tr.log(tr.result['stopped_reason'])
                    else:
                        tr.result['stopped_reason'] = 'cup not resting upright after placing (%s)' % placed.get('reason')
                    return
            after = playback.get('finished_unix') or time.time()
            tr.result['cycles_run'] = self.seq
            tr.result['handover_counts'] = dict(self.counts)
        tr.result['stopped_reason'] = 'handover steps exhausted in %s' % self.phase

    def verify_placed(self, hold_s=3.):
        """Cup back on the table: near its start height, upright, held by neither gripper, still."""
        tr = self.tr
        start, best, last = None, 0., {}
        until = time.monotonic()+hold_s+1.
        while time.monotonic() < until:
            status = tr.status() or {}
            p = status.get('props', {}).get(tr.v['target']['label'], {})
            holders = (status.get('friction_grip') or {}).get('holding') or {}
            ok = (-1. <= p.get('height_above_start_cm', 99) <= 1.5 and 0 <= p.get('tilt_degrees', 180) <= 10
                  and p.get('speed_cm_s', 99) <= 2 and tr.v['target']['label'] not in holders.values())
            now = time.monotonic()
            start = (start or now) if ok else None
            best = max(best, now-start) if start else best
            last = {'height_cm': round(p.get('height_above_start_cm', 0), 2), 'tilt_degrees': round(p.get('tilt_degrees', 0), 1),
                    'held_by': [k for k, v in holders.items() if v == tr.v['target']['label']]}
            if best >= hold_s:
                break
            time.sleep(.1)
        return {'passed': best >= hold_s, 'held_s': round(best, 2), **last,
                'reason': None if best >= hold_s else 'height %s cm, tilt %s deg, held by %s' % (last.get('height_cm'), last.get('tilt_degrees'), last.get('held_by')),
                'scope': 'cup resting on the table, upright, released, gravity on'}

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
