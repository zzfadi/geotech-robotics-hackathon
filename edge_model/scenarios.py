"""The three scoring scenarios on the kinematic bench (numpy only).

autonomous  the policy drives alone (or the expert, or the untrained 'before' net)
lag         a chunked teacher whose replies arrive late, hold-on-late vs model-bridges
assist      a simulated operator with centimetres of aim error, alone vs assist blending

All of them run on BenchTable (kinematic, not physics), command the arm in
absolute joint targets at 5 Hz, clamp commanded tool speed to 0.85 m/s, score
the target cup with gate.py and can write a control-center session per attempt.

Lag-loop protocol (documented in edge_model/README.md):
  - The teacher is the scripted expert, not Dream. Each reply is a chunk of 12
    bench steps (2.4 s = 48 Dream-rate actions at 20 Hz; one bench step = 4).
  - A request carries the arm state and the commands already committed; the
    teacher plans the chunk as the continuation from the end of that queue
    (as in real-time chunking), so an accepted chunk is always played from
    its first action: nothing is ever skipped (chunk.skipped == 0).
  - Async while the median of the last 5 round trips is <= 850 ms: the next
    request goes out with 8 steps (32 actions, 1.6 s) still queued. Sync above
    850 ms: the next request goes out only when the queue is empty.
  - Round trips: a two-state (normal / congested) Markov chain per request,
    lognormal around 685 ms or 1250 ms (overall median about 712 ms), with occasional failures (no reply;
    detected after a 2.5 s timeout). The same request index gets the same
    round trip in both variants (common random numbers).
  - hold-on-late: an empty queue means the arm holds its pose (frozen) until
    a reply lands.
  - model-bridges: an empty queue means the edge model commits a short bridge
    (its own open-loop rollout through the arm FK, about one expected round
    trip long), and a fresh request goes out planned from the bridge's end.
    A reply planned before the arm moved is dropped, never resumed. Bridging
    is capped at 4 s in a row; after that the loop holds until a reply lands.
"""
import copy
import math
import time

import numpy as np

from edge_model.bench import DT, GAP_MAX_M, GAP_SPEED, JOINT_SPEED, BenchTable, clone, cup_radius, CUP_HEIGHT
from edge_model.collect import cup_estimate, episode_rng, outcome
from edge_model.expert import Expert
from edge_model.kinematics import HOME

SPEED_LIMIT = 0.85
CHUNK_STEPS = 12          # 48 actions at 20 Hz = 2.4 s
ACTIONS_PER_STEP = 4
ASYNC_LEAD_STEPS = 8      # request with 32 actions (1.6 s) left
THRESHOLD_MS = 850
WINDOW = 5
TIMEOUT_S = 2.5
BRIDGE_CAP_S = 4.0


# ---------------------------------------------------------------- shared helpers
def clamp_speed(arm, q, q_target, limit=SPEED_LIMIT):
    """Scale the joint step so the commanded tool speed stays under the limit."""
    q_target = arm.clip(q_target)
    p0, _ = arm.tcp(q)
    p1, _ = arm.tcp(q_target)
    requested = float(np.linalg.norm(p1 - p0)) / DT
    if requested <= limit:
        return q_target, requested, requested, False
    scale = limit / requested
    for _ in range(4):
        qa = q + (q_target - q) * scale
        applied = float(np.linalg.norm(arm.tcp(qa)[0] - p0)) / DT
        if applied <= limit + 1e-9:
            break
        scale *= 0.95 * limit / applied
    return qa, requested, applied, True


class ModelDriver:
    """Closed-loop policy: q_target = q + dq, grip open when p(open) > 0.5."""

    def __init__(self, policy, est):
        self.policy, self.est = policy, np.asarray(est, dtype=float)
        self.latency_ms = []

    def predict(self, obs):
        t0 = time.perf_counter()
        dq, g = self.policy.predict(obs)
        self.latency_ms.append((time.perf_counter() - t0) * 1000)
        return np.asarray(dq, dtype=float), float(g)

    def act(self, table):
        dq, g = self.predict(table.observe(self.est))
        return table.q + dq, (1.0 if g > 0.5 else 0.0), 'model'

    def rollout(self, arm, q, grip_meas, steps, grip_cmd=None):
        """Open-loop plan from the arm FK only (no simulator): committed bridge targets.
        grip_cmd fixes the grip command (the lag bridge never opens or closes the jaws)."""
        q = np.asarray(q, dtype=float).copy()
        plan = []
        for _ in range(steps):
            p, _ = arm.tcp(q)
            obs = np.r_[q, grip_meas, p, self.est - p, self.est[2]].astype(np.float32)
            dq, g = self.predict(obs)
            cmd = (1.0 if g > 0.5 else 0.0) if grip_cmd is None else grip_cmd
            q_next = arm.clip(q + np.clip(dq, -JOINT_SPEED * DT, JOINT_SPEED * DT))
            plan.append((q_next, cmd))
            q = q_next
            grip_meas = _predict_grip(grip_meas, cmd, self.est, arm.tcp(q)[0])
        return plan


def _predict_grip(grip, cmd, est, tcp):
    """Measured-grip guess for open-loop planning: jaws stop on a cup they surround."""
    step = GAP_SPEED * DT / GAP_MAX_M
    if cmd > 0.5:
        return min(1.0, grip + step)
    z_rel = tcp[2] - (est[2] - CUP_HEIGHT / 2)
    around_cup = np.linalg.norm((tcp - est)[:2]) < 0.02 and 0.0 <= z_rel <= CUP_HEIGHT
    floor = 2 * cup_radius(z_rel) / GAP_MAX_M if around_cup else 0.0
    return max(floor, grip - step)


def _drive(table, choose, steps, log=None, speed=None, after_pass=5, on_step=None):
    """Generic closed loop. choose(table, step) -> (q_target, grip, source)."""
    from edge_model import gate
    times, evidence = [], []
    speed = speed if speed is not None else {'peak_requested': 0.0, 'peak_applied': 0.0, 'clamps': 0}
    stop_at = steps
    for step in range(steps):
        q_target, grip, source = choose(table, step)
        qa, req, app, clamped = clamp_speed(table.arm, table.q, q_target)
        speed['peak_requested'] = max(speed['peak_requested'], req)
        speed['peak_applied'] = max(speed['peak_applied'], app)
        if clamped:
            speed['clamps'] += 1
        if log and (clamped or step % 5 == 0):
            log.command(table.t, req, app, clamped)
        table.step(qa, grip)
        times.append(round((step + 1) * DT, 6))
        evidence.append(table.evidence())
        if on_step:
            on_step(table, step, source)
        if log:
            log.contacts(table)
            log.state(table, step)
        if stop_at == steps:
            g = gate.evaluate(times, evidence)
            if g['passed_at_s'] is not None:
                stop_at = step + after_pass
        if step >= stop_at:
            break
    return times, evidence, speed


def new_table(seed, table_index, salt):
    rng = episode_rng(seed, table_index, 0, salt)
    table = BenchTable(table_index=table_index, seed=seed)
    return table, cup_estimate(table, rng), rng


# ---------------------------------------------------------------- autonomous
def autonomous(seed, table_index, driver='model', policy=None, steps=150, log=None):
    """driver: 'expert' (reference), 'model' (trained) or 'untrained' (before row)."""
    table, est, _ = new_table(seed, table_index, salt=1)
    if driver == 'expert':
        expert = Expert(est)

        def choose(tb, step):
            q, g, phase = expert.act(tb)
            return q, g, phase
        lat = None
    else:
        md = ModelDriver(policy, est)
        lat = md.latency_ms
        choose = lambda tb, step: md.act(tb)  # noqa: E731
    if log:
        log.emit('task', t=0.0, phase='start', name=log.task, detail='Autonomous, %s drives' % driver)
        who = 'controller' if driver == 'expert' else 'edge'
        log.emit('decision', t=0.0, by=who, what='%s drives the whole attempt' % (
            'The scripted expert' if driver == 'expert' else 'The edge model'),
            why='Autonomous scenario: %s alone, cup position from the (noisy) overhead estimate' % driver)
    grip_state = {'last': 1.0}

    def on_step(tb, step, source):
        if log and driver != 'expert':
            g = 1.0 if tb.gap / GAP_MAX_M > 0.95 else 0.0
            if g != grip_state['last'] and g == 0.0:
                log.emit('decision', t=tb.t, by='edge', what='Closed the gripper',
                         why='The tool reached the estimated cup position', routine=True)
            grip_state['last'] = g
    times, evidence, speed = _drive(table, choose, steps, log, on_step=on_step)
    result = outcome(table, times, evidence)
    result.update(speed=speed, steps=len(times))
    if lat:
        result['latency_ms'] = {'median': float(np.median(lat)), 'p99': float(np.percentile(lat, 99)),
                                'max': float(np.max(lat)), 'calls': len(lat)}
    return result


# ---------------------------------------------------------------- lag
class RoundTrips:
    """Dream-like round trips per request index: normal ~685 ms (overall median ~712), congested ~1250 ms, failures."""
    NORMAL_MS, CONGESTED_MS = 685.0, 1250.0
    SIGMA_N, SIGMA_C = 0.12, 0.20
    P_ENTER, P_EXIT = 0.08, 0.30
    P_FAIL_N, P_FAIL_C = 0.02, 0.06

    def __init__(self, seed, table_index, count=400):
        rng = np.random.default_rng([int(seed), int(table_index), 9090])
        congested = False
        self.values, self.congested = [], []
        for _ in range(count):
            congested = (rng.random() < self.P_ENTER) if not congested else (rng.random() >= self.P_EXIT)
            fail = rng.random() < (self.P_FAIL_C if congested else self.P_FAIL_N)
            med, sig = (self.CONGESTED_MS, self.SIGMA_C) if congested else (self.NORMAL_MS, self.SIGMA_N)
            rtt = max(380.0, float(rng.lognormal(math.log(med), sig)))
            self.values.append(None if fail else rtt)
            self.congested.append(congested)

    def __getitem__(self, k):
        return self.values[k]

    @classmethod
    def describe(cls):
        return {'normal_median_ms': cls.NORMAL_MS, 'normal_sigma_log': cls.SIGMA_N,
                'congested_median_ms': cls.CONGESTED_MS, 'congested_sigma_log': cls.SIGMA_C,
                'p_enter_congestion': cls.P_ENTER, 'p_exit_congestion': cls.P_EXIT,
                'p_fail_normal': cls.P_FAIL_N, 'p_fail_congested': cls.P_FAIL_C,
                'floor_ms': 380.0, 'timeout_s': TIMEOUT_S}


def lag(seed, table_index, variant='hold', policy=None, steps=225, log=None):
    """variant 'hold' (hold-on-late) or 'bridge' (edge model bridges the gap)."""
    from edge_model import gate
    table, est, _ = new_table(seed, table_index, salt=2)
    rtts = RoundTrips(seed, table_index)
    teacher = Expert(est)
    teacher.q_pre = teacher._pregrasp_joints(table.arm, table.q)   # planned once, from the start pose
    md = ModelDriver(policy, est) if variant == 'bridge' else None
    queue = []                 # committed commands: (q_target, grip, source)
    pending = []               # requests in flight
    seen_rtts, mode = [], 'async'
    stats = {'frozen_steps': 0, 'frozen_before_first_reply_steps': 0, 'bridge_steps': 0, 'requests': 0,
             'late': 0, 'failed': 0, 'stale_dropped': 0, 'chunks_played': 0, 'teacher_actions_played': 0,
             'skipped_actions': 0, 'bridges': 0, 'bridge_cap_hits': 0}
    last_grip = [1.0]
    bridge_run = [0]           # consecutive bridge steps
    current = {'rid': None, 'played': 0}
    if log:
        log.emit('task', t=0.0, phase='start', name=log.task,
                 detail='Lag scenario, %s' % ('model bridges the gap' if variant == 'bridge' else 'hold on late'))
        log.emit('decision', t=0.0, by='loop', what='Playing the teacher\'s 48-action chunks, async first',
                 why=('When no reply is in hand the edge model bridges the gap' if variant == 'bridge' else
                      'When a reply is late the arm holds its pose until it lands') +
                     '; chunks are never skipped ahead and speed is clamped to 0.85 m/s')

    def plan(rid_index):
        """Teacher chunk planned from the end of the committed queue (continuation)."""
        twin = clone(table)
        for q_t, g, _ in queue:
            twin.step(clamp_speed(twin.arm, twin.q, q_t)[0], g)
        e = copy.deepcopy(teacher_box[0])
        chunk = []
        for _ in range(CHUNK_STEPS):
            q_t, g, _phase = e.act(twin)
            chunk.append((q_t, g, 'teacher'))
            twin.step(clamp_speed(twin.arm, twin.q, q_t)[0], g)
        return chunk, e

    teacher_box = [teacher]

    def send(t):
        k = stats['requests']
        stats['requests'] += 1
        rid = 'r%04d' % k
        chunk, e_final = plan(k)
        rtt = rtts[k]
        req = {'rid': rid, 'sent': t, 'rtt_ms': rtt, 'arrive': None if rtt is None else t + rtt / 1000.0,
               'fail_at': t + TIMEOUT_S if rtt is None else None, 'chunk': chunk, 'teacher': e_final,
               'queue_len': len(queue), 'valid': True, 'late_logged': False}
        pending.append(req)
        if log:
            log.emit('network', t=t, phase='sent', request_id=rid, model='bench-expert-teacher',
                     upload_bytes=None)
        return req

    def update_mode(t):
        nonlocal mode
        if len(seen_rtts) < WINDOW:
            return
        med = float(np.median(seen_rtts[-WINDOW:]))
        want = 'sync' if med > THRESHOLD_MS else 'async'
        if want != mode:
            if log:
                log.emit('mode', t=t, mode=want, previous=mode, median_ms=round(med, 1), threshold_ms=THRESHOLD_MS,
                         window=WINDOW, reason='Median of the last 5 round trips is %d ms' % med)
                log.emit('decision', t=t, by='loop', what='Switched to %s mode' % want,
                         why=('Replies are late: median %d ms over %d ms, so wait for each one' if want == 'sync'
                              else 'Replies are on time again: median %d ms under %d ms') % (med, THRESHOLD_MS))
            mode = want

    frozen = {'run_ms': 0.0, 'announced': False}

    def can_bridge():
        # Only an open, empty gripper is bridged; closing, holding or lifting waits (holds) instead.
        return (variant == 'bridge' and bridge_run[0] * DT < BRIDGE_CAP_S - 1e-9
                and table.gap >= 0.95 * GAP_MAX_M and last_grip[0] > 0.5)

    def choose(tb, step):
        t = tb.t
        # 1. replies and failures that are due by now
        for req in list(pending):
            if req['arrive'] is not None and req['arrive'] <= t + 1e-9:
                pending.remove(req)
                seen_rtts.append(req['rtt_ms'])
                if log:
                    log.emit('network', t=req['arrive'], phase='received', request_id=req['rid'],
                             rtt_ms=round(req['rtt_ms'], 1), model='bench-expert-teacher')
                if not req['valid']:
                    stats['stale_dropped'] += 1
                    if log:
                        log.emit('decision', t=t, by='loop', what='Dropped the late reply %s' % req['rid'],
                                 why='The edge model moved the arm after it was requested; a late chunk is '
                                     'never resumed after the local state has moved', request_id=req['rid'])
                    update_mode(t)
                    continue
                if log:
                    log.emit('chunk', t=t, request_id=req['rid'], actions=CHUNK_STEPS * ACTIONS_PER_STEP,
                             arrived_at_action=int(min(current['played'], CHUNK_STEPS) * ACTIONS_PER_STEP),
                             blended=0, skipped=0, waited_ms=round(frozen['run_ms']))
                queue.extend(req['chunk'])          # played from its first action: never skips ahead
                teacher_box[0] = req['teacher']
                stats['chunks_played'] += 1
                frozen['run_ms'], frozen['announced'] = 0.0, False
                current['rid'], current['played'] = req['rid'], 0
                update_mode(t)
            elif req['fail_at'] is not None and req['fail_at'] <= t + 1e-9:
                pending.remove(req)
                stats['failed'] += 1
                if log:
                    log.emit('network', t=req['fail_at'], phase='failed', request_id=req['rid'],
                             error='No reply within %.1f s' % TIMEOUT_S)
                    log.emit('decision', t=t, by='loop', what='Request %s failed; asking again' % req['rid'],
                             why='No reply within the %.1f s timeout' % TIMEOUT_S, request_id=req['rid'])
        # 2. empty queue: late replies, then bridge (model) or hold
        if not queue:
            for req in pending:
                if req['valid'] and not req['late_logged'] and req['rid'] != 'r0000':
                    req['late_logged'] = True
                    stats['late'] += 1
                    if log:
                        log.emit('network', t=t, phase='late', request_id=req['rid'])
            if can_bridge():
                expected = float(np.median(seen_rtts[-WINDOW:])) if seen_rtts else RoundTrips.NORMAL_MS
                room = int(round((BRIDGE_CAP_S - bridge_run[0] * DT) / DT))
                n = max(1, min(int(math.ceil(expected / 1000.0 / DT)) + 1, room))
                for req in pending:
                    req['valid'] = False      # planned before the arm moves: dropped on arrival
                # The bridge moves the joints only; the grip stays as last commanded.
                queue.extend((q_t, g, 'edge') for q_t, g in
                             md.rollout(tb.arm, tb.q, tb.grip(), n, grip_cmd=last_grip[0]))
                stats['bridges'] += 1
                if log:
                    log.emit('decision', t=t, by='edge', what='Bridging %.1f s with the edge model' % (n * DT),
                             why='No teacher reply in hand; the edge model keeps the arm moving and the next '
                                 'request is planned from where the bridge ends')
                send(t)
            elif variant == 'bridge' and not frozen['announced'] and bridge_run[0] * DT >= BRIDGE_CAP_S - 1e-9:
                stats['bridge_cap_hits'] += 1
                if log:
                    log.emit('decision', t=t, by='safety', what='Holding: bridge limit reached',
                             why='The edge model bridged %.0f s in a row; a learned policy never replaces '
                                 'the hold' % BRIDGE_CAP_S)
        # 3. ask for the next chunk when none is in flight
        if not any(r['valid'] for r in pending):
            if (mode == 'async' and len(queue) <= ASYNC_LEAD_STEPS) or (mode == 'sync' and not queue):
                send(t)
        if not queue:
            if log and not frozen['announced'] and stats['requests'] > 1:
                log.emit('decision', t=t, by='loop', what='Holding position until the reply lands',
                         why='The queue ran out before the reply; the loop never skips ahead')
            frozen['announced'] = True
            frozen['run_ms'] += DT * 1000
            stats['frozen_steps'] += 1
            if not stats['chunks_played']:
                stats['frozen_before_first_reply_steps'] += 1   # waiting for the very first chunk
            return tb.q.copy(), last_grip[0], 'hold'
        q_t, g, source = queue.pop(0)
        current['played'] += 1
        if source == 'teacher':
            stats['teacher_actions_played'] += 1
        if source == 'edge':
            stats['bridge_steps'] += 1
            bridge_run[0] += 1
        else:
            bridge_run[0] = 0
        last_grip[0] = g
        return q_t, g, source

    times, evidence, speed = _drive(table, choose, steps, log)
    # Measured, not assumed: every accepted chunk step was either played or is still queued at the end.
    still_queued = sum(1 for _, _, source in queue if source == 'teacher')
    stats['skipped_actions'] = ACTIONS_PER_STEP * (stats['chunks_played'] * CHUNK_STEPS
                                                   - stats['teacher_actions_played'] - still_queued)
    result = outcome(table, times, evidence)
    got = [r for r in seen_rtts]
    first_wait = stats['frozen_before_first_reply_steps']
    result.update(speed=speed, steps=len(times), lag=dict(
        stats, frozen_s=round(stats['frozen_steps'] * DT, 2), bridge_s=round(stats['bridge_steps'] * DT, 2),
        frozen_before_first_reply_s=round(first_wait * DT, 2),
        frozen_after_first_reply_s=round((stats['frozen_steps'] - first_wait) * DT, 2),
        rtt_received=len(got), rtt_median_ms=round(float(np.median(got)), 1) if got else None,
        time_to_hold_s=None if result['passed_at_s'] is None else round(result['passed_at_s'] - gate.HOLD_S, 2)))
    if md is not None and md.latency_ms:
        result['latency_ms'] = {'median': float(np.median(md.latency_ms)),
                                'p99': float(np.percentile(md.latency_ms, 99)), 'calls': len(md.latency_ms)}
    return result


# ---------------------------------------------------------------- assist
ASSIST_ZONE_M = 0.08       # assist engages when the tool comes this close to the detected cup
ASSIST_RAMP_STEPS = 3      # and blends from operator to model over 0.6 s, then keeps authority
AIM_ERROR_M = (0.02, 0.04)


class Assist:
    """Snap-to-cup assist: inside the zone the model's command is blended in over
    ASSIST_RAMP_STEPS and then keeps authority until the jaws hold a cup; after the
    grasp the operator has the arm back (lift and hold). A blend that hands
    authority back and forth fights the operator's offset line, so it latches."""

    def __init__(self, driver, est):
        self.driver, self.est = driver, np.asarray(est, dtype=float)
        self.alpha, self.latched, self.alphas = 0.0, False, []
        self.last_grip = 1.0
        self.closed_steps = 0

    def holding(self, table):
        # Jaws commanded shut for 3 steps (0.6 s, longer than the close takes) and stopped on something.
        return self.closed_steps >= 3 and 0.045 < table.gap < 0.95 * GAP_MAX_M

    def blend(self, table, q_op, g_op):
        p, _ = table.arm.tcp(table.q)
        d = float(np.linalg.norm(p - self.est))
        if self.holding(table):
            self.latched, self.alpha = False, 0.0
        elif not self.latched and d < ASSIST_ZONE_M:
            self.latched = True
        if self.latched:
            self.alpha = min(1.0, self.alpha + 1.0 / ASSIST_RAMP_STEPS)
        self.alphas.append(self.alpha)
        if self.alpha <= 0:
            q, g = q_op, g_op
        else:
            q_m, g_m, _ = self.driver.act(table)
            q = (1 - self.alpha) * q_op + self.alpha * q_m
            g = g_m if self.alpha >= 0.5 else g_op
        self.last_grip = g
        self.closed_steps = self.closed_steps + 1 if g == 0.0 else 0
        return q, g, d


def assist(seed, table_index, variant='alone', policy=None, steps=150, log=None):
    """variant 'alone' (operator only) or 'assist' (model snaps the last centimetres)."""
    table, est, rng = new_table(seed, table_index, salt=3)
    angle = rng.uniform(0, 2 * math.pi)
    err = rng.uniform(*AIM_ERROR_M)
    aim = est.copy()
    aim[:2] += err * np.array([math.cos(angle), math.sin(angle)])
    operator = Expert(aim)      # a person aiming a few cm off: same motions, wrong spot
    helper = Assist(ModelDriver(policy, est), est) if variant == 'assist' else None
    flags = {'announced': False, 'handed_back': False}
    if log:
        log.emit('task', t=0.0, phase='start', name=log.task,
                 detail='Assist scenario, operator aims %.1f cm off, %s' % (err * 100, variant))
        log.emit('decision', t=0.0, by='operator', what='Operator drives toward the cup',
                 why='Simulated operator with %.1f cm of aim error' % (err * 100))

    def choose(tb, step):
        q_op, g_op, phase = operator.act(tb)
        if helper is None:
            return q_op, g_op, 'operator'
        q, g, d = helper.blend(tb, q_op, g_op)
        if log and helper.latched and not flags['announced']:
            flags['announced'] = True
            log.emit('decision', t=tb.t, by='assist', what='Assist blending in near the cup',
                     why='The tool is %.1f cm from the detected cup; assist makes the last moves' % (d * 100))
        if log and flags['announced'] and not flags['handed_back'] and helper.holding(tb):
            flags['handed_back'] = True
            log.emit('decision', t=tb.t, by='assist', what='Handed the arm back to the operator',
                     why='Both jaws are closed on the cup; the operator lifts')
        return q, g, ('assist' if helper.alpha > 0 else 'operator')

    times, evidence, speed = _drive(table, choose, steps, log)
    result = outcome(table, times, evidence)
    result.update(speed=speed, steps=len(times), aim_error_cm=round(err * 100, 2),
                  assist_steps=int(sum(a > 0 for a in helper.alphas)) if helper else 0)
    if helper is not None and helper.driver.latency_ms:
        lat = helper.driver.latency_ms
        result['latency_ms'] = {'median': float(np.median(lat)), 'p99': float(np.percentile(lat, 99)),
                                'calls': len(lat)}
    return result


__all__ = ['autonomous', 'lag', 'assist', 'RoundTrips', 'clamp_speed', 'ModelDriver', 'HOME']
