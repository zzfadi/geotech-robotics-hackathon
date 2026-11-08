#!/usr/bin/env python3
"""Table 10 Dream runner for the VR cup handover: request.json -> Dream pickup -> status.json.

In VR, Denys places the cup on Table 10 and says "take the cup". The VR session's voice trigger writes
Saved/Table10Dream/request.json {id, prompt, unix, source, table: 10}. This runner:
  1. picks the arm on the cup's side, plans a reach to the cup (labelled: planned, no Dream call),
  2. Dream grasp: Dream sets the jaw closing while the arm is held still (labelled),
  3. Dream lift: only Dream's upward motion is applied, the cup is kept level (labelled),
  4. reports progress in Saved/Table10Dream/status.json {id, state, detail, unix}.
Observations and motion go through Tools/maker_arm_table10_dream.py in the editor world, the world VR shows.
The cup is carried kinematically there ("editor-world kinematic carry"). Table 10's arm poses reach the real
arms only when the real-arm service is in drive.

Run with the Dream key from the launcher (never handled here):
  ~/.codex/bin/op-agents run -- uv run --with dreamlabs-client==1.1.3 --with dreamlabs==1.0.37 --with scipy \
      python -u DreamSim/table10_dream.py
"""
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Unreal/TheHumanTouch/Tools'))  # maker_arm_model for pregrasp
PROJECT = Path('/Users/geo_dennis/Desktop/Projects/geotech-robotics-hackathon/geotech-robotics-hackathon/Unreal/TheHumanTouch')
DIR = PROJECT / 'Saved/Table10Dream'
TOOLS = PROJECT / 'Tools'
JAW = .104825
REQUEST_FRESH_S = 30.
LIFT_TARGET_CM = 10.
PROMPTS = {
    'grasp': 'Close the {side} gripper around the cup and hold it firmly. Keep the {other} arm still.',
    'lift': 'Lift the cup up 5 more centimeters. Keep the {other} arm still.',
}
START_EDITOR = '''import sys, importlib, json
T = r"%s"
T in sys.path or sys.path.insert(0, T)
import unreal, maker_arm_table10_dream as t10
t10 = importlib.reload(t10)
state = {}
def _go(delta):
    unreal.unregister_slate_post_tick_callback(state["h"])
    try:
        t10.start(r"%s")
    except Exception as error:
        unreal.log_error("Table 10 Dream runtime start: %%s" %% error)
state["h"] = unreal.register_slate_post_tick_callback(_go)
print("T10DREAM scheduled")
''' % (TOOLS, DIR)


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError, OSError):
        return None


def write(path, value):
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(value))
    tmp.replace(path)


class Runner:
    def __init__(self):
        DIR.mkdir(parents=True, exist_ok=True)
        self.client = None
        self.seq = 0
        self.request = None

    # ---- plumbing ---------------------------------------------------------------------------
    def status(self, state, detail=''):
        write(DIR / 'status.json', {'id': (self.request or {}).get('id'), 'state': state, 'detail': detail, 'unix': time.time()})
        print('[%s] %s' % (state, detail), flush=True)

    def ensure_editor(self):
        cup = read(DIR / 'cup.json')
        if cup and time.time() - cup.get('unix', 0) < 2:
            return
        out = subprocess.run(['uv', 'run', '-q', '--script', 'Tools/copresence_mcp.py', 'exec-python', '8041', START_EDITOR],
                             cwd=PROJECT, capture_output=True, text=True, timeout=60)
        for _ in range(50):
            time.sleep(.2)
            cup = read(DIR / 'cup.json')
            if cup and time.time() - cup.get('unix', 0) < 2:
                return
        raise RuntimeError('Table 10 Dream runtime did not start in the editor: ' + (out.stdout + out.stderr)[-300:])

    def ensure_client(self):
        if self.client is not None:
            return
        from dream import DreamClient
        from model import prepare
        from probe import configure
        self.status('starting', 'connecting to Dream')
        arm = prepare(DIR / 'model', tcp_back=0.)
        client = DreamClient()
        configure(client, arm, 'dreamlabs/dream-1-beta')
        if not client.wait_for_ik(240):
            client.close()
            raise TimeoutError('Dream IK initialization timed out')
        self.client = client

    def observe(self):
        after = time.time()
        (DIR / 'need_observation').touch()
        for _ in range(100):
            time.sleep(.05)
            packet = read(DIR / 'observation.json')
            if packet and packet.get('captured_unix', 0) > after:
                return packet
        raise TimeoutError('no Table 10 observation from the editor')

    def play(self, action, label):
        self.seq += 1
        n = max(len(action['q_l_chunk']), len(action['q_r_chunk']))
        for key in ('q_l_chunk', 'q_r_chunk'):
            while len(action[key]) < n:
                action[key].append(list(action[key][-1]))
        report = DIR / ('action-%03d.json' % self.seq)
        write(report, {'action': action, 'controller_source': label})
        write(DIR / 'command.json', {'seq': self.seq, 'report': str(report)})
        deadline = time.time() + n / 20. + 15
        while time.time() < deadline:
            done = read(DIR / 'playback.json')
            if done and done.get('seq') == self.seq and done.get('done'):
                if done.get('error'):
                    raise RuntimeError('editor playback failed: ' + done['error'])
                return done
            time.sleep(.05)
        raise TimeoutError('Table 10 motion did not finish')

    def dream(self, packet, prompt, side):
        from dream.errors import DreamSessionExpired
        from position_ik import adapt
        from fleet_worker import constrain_motion_side
        for attempt in range(4):
            obs = {'prompt': prompt}
            for s in ('left', 'right'):
                obs['state.%s_arm' % s] = packet['state']['q_' + s]
                obs['state.%s_grip' % s] = [packet['state']['gap_%s_m' % s] / JAW]
            try:
                self.client.set_observation(packet['frames'], obs, timestamp=packet['captured_monotonic'])
                action = self.client.get_action()
                break
            except DreamSessionExpired:
                packet = self.observe()  # session renewed: retry from fresh feedback
            except Exception as error:
                if 'NO_CAPACITY' not in str(error) and 'SVD' not in str(error) or attempt == 3:
                    raise
                time.sleep(2 ** attempt)
                packet = self.observe()
        action, _ = adapt(action, packet['state'], 12, active_sides=[side], allow_prefix=True)
        return constrain_motion_side(action, packet['state'], side), packet

    # ---- the pickup -------------------------------------------------------------------------
    def pickup(self):
        from pregrasp import approach_action, vertical_only, tool_height
        cup = read(DIR / 'cup.json') or {}
        if not cup.get('present'):
            raise RuntimeError('no MakerInteraction_T09__HandoverCup on Table 10')
        x, y, z = cup['table_local_cm']
        hold = lambda st, s, n: [list(st['q_' + s]) + [st['gap_%s_m' % s] / JAW] for _ in range(n)]
        # 1. planned reach to the cup where Denys placed it, with whichever arm can reach it (nearest first)
        packet = self.observe()
        action = side = None
        for side in sorted(('left', 'right'), key=lambda s: abs(y - (-40 if s == 'left' else 40))):
            for pitch in (45., 55., 60., 50.):
                variant = {'target': {'label': 'HandoverCup', 'location_cm': [x, y, 81.0]}, 'objective': {'side': side},
                           'assist': {'pitch_deg': pitch, 'gap_m': .1}}
                try:
                    action, info = approach_action(packet['state'], variant)
                    break
                except ValueError:
                    continue
            if action is not None:
                break
        if action is None:
            raise RuntimeError('cup at (%.0f, %.0f) cm is out of both arms\' reach; place it nearer an arm '
                               '(centre line: x 30-50 cm from the arm bases)' % (x, y))
        other = 'right' if side == 'left' else 'left'
        key, okey = ('q_l_chunk', 'q_r_chunk') if side == 'left' else ('q_r_chunk', 'q_l_chunk')
        self.status('running', 'reaching for the cup with the %s arm (planned reach)' % side)
        self.play(action, 'planned reach (%s, no Dream call)' % side)
        # 2. Dream grasp, arm held still
        for cycle in range(6):
            packet = self.observe()
            self.status('running', 'Dream: grasping the cup (%s arm)' % side)
            action, packet = self.dream(packet, PROMPTS['grasp'].format(side=side, other=other), side)
            still = list(packet['state']['q_' + side])
            g0 = packet['state']['gap_%s_m' % side] / JAW
            g1 = min(min(r[6] for r in action[key]), g0)
            if cycle >= 3:
                g1 = min(g1, .45)  # labelled: Dream kept the jaws open; close gently
            rows = [still + [g0 + (g1 - g0) * k / 29] for k in range(30)]
            self.play({key: rows, okey: hold(packet['state'], other, 30)}, 'Dream grip, arm held still (%s)' % side)
            if (read(DIR / 'cup.json') or {}).get('held_by') == side:
                break
        else:
            raise RuntimeError('the %s jaws never closed on the cup' % side)
        # Grip latch: 6 mm tighter than where the jaws stopped on the cup (as in the fleet pickups)
        latch = max((read(DIR / 'playback.json') or {}).get('gap_%s_m' % side, .06) - .006, 0.) / JAW
        # 3. Dream lift: only Dream's upward motion, cup kept level
        floor = None
        for cycle in range(14):
            packet = self.observe()
            floor = floor or tool_height(packet['state']['q_' + side])
            cup = read(DIR / 'cup.json') or {}
            if cup.get('held_by') != side:
                raise RuntimeError('the cup slipped out of the %s gripper' % side)
            if cup.get('height_above_table_cm', 0) >= LIFT_TARGET_CM:
                break
            self.status('running', 'Dream: lifting the cup (%.1f / %.0f cm)' % (cup.get('height_above_table_cm', 0), LIFT_TARGET_CM))
            action, packet = self.dream(packet, PROMPTS['lift'].format(other=other), side)
            action, info = vertical_only(action, packet['state'], side, floor_z=floor, allow_down_cm=0., max_step_cm=.15,
                                         up_gain=2.5, min_rise_cm=1.)
            action[key] = [r[:6] + [latch] for r in action[key]]
            action[okey] = hold(packet['state'], other, len(action[key]))
            self.play(action, 'Dream lift, vertical only (%s)' % side)
        cup = read(DIR / 'cup.json') or {}
        return '%s arm holding the cup %.1f cm above the table (Dream grasp and lift; editor-world kinematic carry)' % (
            side, cup.get('height_above_table_cm', 0))

    def put_down(self):
        """Open both grippers (the cup is set down upright on the table), then both arms go to idle (planned)."""
        from fleet_reset import HOME_Q
        packet = self.observe()
        st = packet['state']
        self.status('running', 'opening the gripper: setting the cup down')
        rows = {s: [list(st['q_' + s]) + [st['gap_%s_m' % s] / JAW + (.95 - st['gap_%s_m' % s] / JAW) * k / 14] for k in range(15)]
                for s in ('left', 'right')}
        self.play({'q_l_chunk': rows['left'], 'q_r_chunk': rows['right']}, 'planned release (no Dream call)')
        packet = self.observe()
        st = packet['state']
        ease = [.5 - .5 * math.cos(math.pi * k / 39) for k in range(40)]
        home = {s: [list(np.array(st['q_' + s]) * (1 - e) + np.array(HOME_Q) * e) + [.95] for e in ease] for s in ('left', 'right')}
        self.status('running', 'arms returning to idle')
        self.play({'q_l_chunk': home['left'], 'q_r_chunk': home['right']}, 'planned return to idle (no Dream call)')
        return 'cup set down; arms at idle'

    def run(self):
        print('Table 10 Dream runner: watching %s' % (DIR / 'request.json'), flush=True)
        handled = None
        while True:
            request = read(DIR / 'request.json')
            if request and request.get('id') != handled:
                handled = request.get('id')
                if time.time() - float(request.get('unix', 0)) > REQUEST_FRESH_S:
                    print('ignoring stale request %s' % handled, flush=True)
                else:
                    self.request = request
                    self.status('queued', 'received "%s" from %s' % (request.get('prompt'), request.get('source')))
                    try:
                        self.ensure_editor()
                        prompt = str(request.get('prompt', '')).lower()
                        if any(word in prompt for word in ('put', 'release', 'drop', 'reset', 'let go')):
                            self.status('done', self.put_down())
                        else:
                            self.ensure_client()
                            self.status('done', self.pickup())
                    except Exception as error:
                        self.status('failed', str(error)[:300])
            time.sleep(.2)


if __name__ == '__main__':
    try:
        Runner().run()
    except KeyboardInterrupt:
        pass
