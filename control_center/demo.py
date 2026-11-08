"""Demo stream: a simulated arm running "hold my beer" through a Dream-style loop.

Everything here is made up and every session it writes says purpose "demo",
which the control center shows as a banner. Its timing follows the Dream
workshop numbers so the views have realistic shapes: a normal round trip near
712 ms, congestion pushing the median of the last 5 past 850 ms (switch to
sync), 48-action chunks at 20 Hz with the reply landing near action 30 and 16
actions blended, a 0.85 m/s speed clamp, 320x176 frames, and one silent
camera fault (red and blue swapped) that the camera checks catch.
"""
import math
import random
import statistics
import time

from control_center.frames import HEIGHT, WIDTH, overhead
from control_center.telemetry import Writer

ACTION_S = 0.05            # 20 Hz actions, so 48 actions = 2.4 s
CHUNK = 48
BLEND = 16
THRESHOLD_MS = 850
WINDOW = 5
SPEED_LIMIT = 0.85
STEPS = [  # (step name, chunks, typical peak speed m/s, what the controller says on entry)
    ('Reach toward the cup', 3, 0.42, 'Cup found in the head camera; heading for it'),
    ('Close the gripper', 1, 0.08, 'Gripper centred on the cup'),
    ('Lift 10 cm', 2, 0.12, 'Both jaws report contact'),
    ('Hold steady 5 s', 2, 0.02, 'Cup is 10 cm up and upright'),
    ('Return to rest', 2, 0.38, 'Hold complete'),
]
CUPS = [(0.42, 0.30), (0.60, 0.62), (0.78, 0.36), (0.30, 0.74)]


class Clock:
    """Virtual time. live=True sleeps so events land in real time (scaled)."""

    def __init__(self, start, live, speed):
        self.t, self.live, self.speed = start, live, speed
        self.wall0, self.t0 = time.time(), start

    def advance(self, seconds):
        self.t += seconds
        if self.live:
            due = self.wall0 + (self.t - self.t0) / self.speed
            delay = due - time.time()
            if delay > 0:
                time.sleep(delay)

    def __call__(self):
        return self.t


class DemoRobot:
    def __init__(self, writer, clock, rng, faults=True):
        self.w, self.clock, self.rng, self.faults = writer, clock, rng, faults
        self.rtts = []
        self.mode = 'async'
        self.req = 0
        self.tcp = [0.12, 0.85]
        self.grip_open = True
        self.held = None
        self.lift = 0.0
        self.colors_swapped = False
        self.clamps = 0
        self.congested_until = None

    # ------------------------------------------------------------ helpers
    def emit(self, kind, **f):
        return self.w.emit(kind, **f)

    def rtt(self):
        t = self.clock()
        if self.congested_until is None and self.rng.random() < 0.012:
            self.congested_until = t + self.rng.uniform(14, 26)
        congested = self.congested_until is not None and t < self.congested_until
        if self.congested_until is not None and t >= self.congested_until:
            self.congested_until = None
        median = 1240 if congested else 705
        rtt = self.rng.lognormvariate(math.log(median), 0.18 if congested else 0.12)
        if congested and self.rng.random() < 0.15:
            rtt *= 1.5                      # the odd reply that misses even a late chunk
        return max(380.0, rtt)

    def cameras(self):
        for role in ('head', 'wrist_l', 'wrist_r'):
            bad = role == 'head' and self.colors_swapped
            checks = {'colors': 'fail' if bad else 'ok', 'orientation': 'ok', 'role_view': 'ok',
                      'present': 'ok', 'aspect': 'ok'}
            self.emit('camera', role=role, ok=not bad, checks=checks, fps=round(self.rng.uniform(29.2, 30.0), 1),
                      age_ms=round(self.rng.uniform(18, 46)), resolution=[640, 480],
                      note='Red and blue look swapped' if bad else None)

    def frame(self, request_id):
        canvas = overhead(self.tcp, self.grip_open, CUPS, held=self.held, lift=self.lift)
        data = canvas.png(swap_red_blue=self.colors_swapped)
        path, rel = self.w.frame_path('%s.png' % request_id)
        with open(path, 'wb') as fh:
            fh.write(data)
        self.emit('frame', role='head', path=rel, width=WIDTH, height=HEIGHT, request_id=request_id, bytes=len(data))

    def state(self):
        q = [round(0.6 * (self.tcp[0] - 0.4) + 0.05 * math.sin(self.clock()), 3), -1.2 + self.lift, 1.5 - self.lift,
             -1.4, 0.0, round(0.3 * (self.tcp[1] - 0.5), 3)]
        self.emit('state', arm='right', q=q, grip=1.0 if self.grip_open else 0.35,
                  tcp=[round(0.2 + self.tcp[0] * 0.3, 3), round(self.tcp[1] * 0.3 - 0.15, 3), round(0.06 + self.lift * 0.12, 3)])

    def play(self, actions, target, speed):
        """Move the sketch toward target over a number of actions, emitting state."""
        start = list(self.tcp)
        for i in range(actions):
            a = (i + 1) / actions
            self.tcp = [start[0] + (target[0] - start[0]) * a, start[1] + (target[1] - start[1]) * a]
            self.clock.advance(ACTION_S)
            if i % 10 == 0:
                self.state()

    # ------------------------------------------------------------ one Dream request
    def request(self, step, peak, target):
        """Send a request, keep playing the current chunk, then handle the reply."""
        if self.mode == 'async':
            self.play(16, target, peak)   # async asks again 16 actions into each chunk
        self.req += 1
        rid = 'r%04d' % self.req
        self.frame(rid)
        upload = 3 * self.rng.randint(26000, 34000)
        self.emit('network', phase='sent', request_id=rid, upload_bytes=upload, frame_size=[WIDTH, HEIGHT],
                  model='dream-1')
        rtt = self.rtt()
        arrive_after = int(round(rtt / 1000 / ACTION_S))
        waited = 0.0
        lead = CHUNK - 16             # actions left in the playing chunk when the request goes
        if self.mode == 'async':
            played = min(arrive_after, lead)
            self.play(played, target, peak)
            if arrive_after > lead:
                waited = (arrive_after - lead) * ACTION_S * 1000
                self.emit('network', phase='late', request_id=rid)
                self.emit('decision', by='loop', what='Holding position until the reply lands',
                          why='The chunk ran out before the reply; the loop never skips ahead', request_id=rid, step=step)
                self.clock.advance(waited / 1000)
            arrived = min(BLEND + arrive_after, CHUNK)
        else:
            waited = rtt                              # sync: ask, wait, then play the whole chunk
            self.clock.advance(rtt / 1000)
            arrived = CHUNK
        self.emit('network', phase='received', request_id=rid, rtt_ms=round(rtt, 1), model='dream-1')
        self.rtts.append(rtt)
        requested = peak * self.rng.uniform(0.85, 1.15)
        if waited > 150 and self.rng.random() < (0.5 if self.mode == 'async' else 0.15):
            requested = self.rng.uniform(1.05, 2.11)   # a late reply asking to catch up
        applied = min(requested, SPEED_LIMIT)
        clamped = requested > SPEED_LIMIT
        self.emit('command', arm='right', requested_m_s=round(requested, 3), applied_m_s=round(applied, 3),
                  limit_m_s=SPEED_LIMIT, clamped=clamped)
        if clamped:
            self.clamps += 1
            self.emit('decision', by='safety', what='Clamped speed from %.2f to %.2f m/s' % (requested, SPEED_LIMIT),
                      why='The reply asked to move faster than the limit after a wait', request_id=rid, step=step)
        blended = BLEND if self.mode == 'async' and not waited else 0
        self.emit('chunk', request_id=rid, actions=CHUNK, arrived_at_action=arrived, blended=blended, skipped=0,
                  waited_ms=round(waited))
        if self.mode == 'sync':
            self.play(CHUNK, target, peak)   # sync plays the whole chunk before asking again
        self.update_mode()

    def update_mode(self):
        if len(self.rtts) < WINDOW:
            return
        median = statistics.median(self.rtts[-WINDOW:])
        want = 'sync' if median > THRESHOLD_MS else 'async'
        if want != self.mode:
            self.emit('mode', mode=want, previous=self.mode, median_ms=round(median, 1), threshold_ms=THRESHOLD_MS,
                      window=WINDOW, reason='Median of the last 5 round trips is %d ms, %s %d ms'
                      % (median, 'over' if want == 'sync' else 'back under', THRESHOLD_MS))
            self.emit('decision', by='loop', what='Switched to %s mode' % want,
                      why=('Replies are late: median %d ms over %d ms, so wait for each one' if want == 'sync'
                           else 'Replies are on time again: median %d ms under %d ms') % (median, THRESHOLD_MS))
            self.mode = want

    # ------------------------------------------------------------ one task
    def task(self, attempt):
        names = [s[0] for s in STEPS]
        self.emit('task', phase='start', name='Hold my beer', steps=names, detail='Attempt %d' % attempt)
        slip = attempt % 5 == 3
        cup = CUPS[0]
        targets = {0: (cup[0] - 0.02, cup[1] + 0.02), 1: cup, 2: cup, 3: cup, 4: (0.12, 0.85)}
        for index, (name, chunks, peak, why) in enumerate(STEPS):
            self.emit('task', phase='step', name='Hold my beer', step=name, step_index=index, steps=names)
            self.emit('decision', by='controller', what='Started: ' + name, why=why, step=name, routine=True)
            for c in range(chunks):
                if name == 'Close the gripper':
                    self.grip_open = False
                if name == 'Lift 10 cm':
                    self.lift = min(1.0, self.lift + 0.5)
                self.request(name, peak, targets[index])
                if slip and name == 'Lift 10 cm' and c == 1:
                    self.emit('contact', what='drop', object='Cup', detail='Slipped from the jaws at 6 cm')
                    self.emit('decision', by='controller', what='Stopped the attempt',
                              why='The cup slipped out during the lift', step=name)
                    self.held, self.lift, self.grip_open = None, 0.0, True
                    self.emit('task', phase='end', name='Hold my beer', outcome='failed', detail='Cup slipped during the lift')
                    self.request('Return to rest', 0.38, (0.12, 0.85))
                    return
            if name == 'Close the gripper':
                self.held = 0
                self.emit('contact', what='grasp', object='Cup', detail='Both jaws in contact')
            if name == 'Return to rest':
                pass
            if name == 'Hold steady 5 s':
                self.held, self.lift, self.grip_open = None, 0.0, True
                self.emit('contact', what='release', object='Cup', detail='Set back down on the table')
        self.emit('task', phase='end', name='Hold my beer', outcome='success', detail='Held 10 cm up for 5 s')

    def run(self, seconds):
        end = self.clock() + seconds
        self.emit('session', phase='start', robot='Maker Arm, right', purpose='demo', station='Table 10',
                  limits={'rtt_threshold_ms': THRESHOLD_MS, 'window': WINDOW, 'speed_limit_m_s': SPEED_LIMIT,
                          'chunk_actions': CHUNK, 'blend_actions': BLEND, 'frame': [WIDTH, HEIGHT]},
                  note='Simulated demo stream; not a real robot')
        self.cameras()
        attempt, next_cam = 0, self.clock() + 10
        fault_at = self.clock() + seconds * 0.45
        fault_fixed = False
        try:
            while self.clock() < end:
                attempt += 1
                if self.faults and not self.colors_swapped and not fault_fixed and self.clock() >= fault_at:
                    self.colors_swapped = True
                    self.cameras()
                    self.emit('decision', by='controller', what='Flagged the head camera',
                              why='Red and blue look swapped; this raises no error on its own')
                self.task(attempt)
                if self.colors_swapped and self.clock() >= fault_at + 25:
                    self.colors_swapped, fault_fixed = False, True
                    self.emit('decision', by='operator', what='Fixed the head camera colour order',
                              why='Checked against the camera checklist')
                    self.cameras()
                if self.clock() >= next_cam:
                    self.cameras()
                    next_cam = self.clock() + 10
                self.clock.advance(2.0)
        finally:
            self.emit('session', phase='end', robot='Maker Arm, right', purpose='demo',
                      note='%d attempts, %d speed clamps' % (attempt, self.clamps))


def run_demo(root, seconds=600, live=True, speed=1.0, seed=None, start=None, faults=True, session=None):
    rng = random.Random(seed)
    clock = Clock(time.time() if start is None else start, live, speed)
    with Writer(root, session=session, component='demo', clock=clock) as writer:
        DemoRobot(writer, clock, rng, faults=faults).run(seconds)
        return writer.session
