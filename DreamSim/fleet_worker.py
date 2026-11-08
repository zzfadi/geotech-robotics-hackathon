"""Run up to 12 Dream trials at once on replica tables in the live editor.

The replica tables are shared booths that extend the hall (maker_arm_fleet);
--scene-only builds them once, later runs only reset start poses.

Each table gets its own Dream session, prompt, checkpoint, controller options,
initial pose and target placement (DreamSim/fleet_variants.json). The editor
side is maker_arm_fleet_runtime: one SIE session, every table physically
simulated at the same time. Uses the editor's single CoPresence task slot, so
it waits for (and blocks) Codex's Dream web worker instead of racing it.

  DreamSim/start_fleet.command                     # real Dream calls
  DreamSim/start_fleet.command --dry-run           # pipeline check, no API calls

Measured lift is evidence only; a successful pickup still needs review of the
recorded wrist/overhead video (physics_simulated, no attachment, held >= 2 s).
"""
import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid

import numpy as np
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO/'Unreal/TheHumanTouch/Tools'))
from maker_arm_fleet_layout import normalize_config, JAW_MAX_M  # noqa: E402
from queueing import wait_for_editor  # noqa: E402
from cup_metrics import cup_metrics, candidate as cup_candidate

STANDARD = 'dreamlabs/dream-1-beta'
# Dream's configure stages a shared temporary URDF; serialize setup, not inference.
_SETUP_LOCK = threading.Lock()
# Ten live table simulations share bounded provider capacity; do not drop excess tables.
_INFERENCE_SLOTS = threading.BoundedSemaphore(4)
DEFAULT_PROJECT = Path('/Users/geo_dennis/Desktop/Projects/geotech-robotics-hackathon/geotech-robotics-hackathon/Unreal/TheHumanTouch')


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, default=lambda x: x.tolist() if hasattr(x, 'tolist') else str(x)))
    tmp.replace(path)


def hold_action(state, rows=12):
    """Dry-run action: hold the observed pose. Exercises the full file/physics loop."""
    return {'q_l_chunk': [list(state['q_left'])+[state['gap_left_m']/JAW_MAX_M]]*rows,
            'q_r_chunk': [list(state['q_right'])+[state['gap_right_m']/JAW_MAX_M]]*rows}


def constrain_motion_side(action, state, side):
    """Hold the inactive arm at measured observation pose, including direct-joint mode."""
    import copy
    if side not in ('left', 'right'):
        raise ValueError('Explicit motion side required')
    active_key = 'q_l_chunk' if side == 'left' else 'q_r_chunk'
    inactive_key = 'q_r_chunk' if side == 'left' else 'q_l_chunk'
    inactive = 'right' if side == 'left' else 'left'
    result = copy.deepcopy(action)
    observed = list(state['q_'+inactive]) + [state['gap_'+inactive+'_m']/JAW_MAX_M]
    result[inactive_key] = [list(observed) for _ in action[active_key]]
    return result


def target_cup_fell(table_status, target, now=None):
    """Conservative terminal failure, not grasp evidence or a recovery trajectory."""
    import math
    if target not in {'MakerStation_Prop_Cup'+suffix for suffix in ('','_02','_03','_04','_05')}:
        return False
    status = table_status or {}
    now = time.time() if now is None else now
    stamp = status.get('sampled_unix')
    prop = status.get('props', {}).get(target, {})
    height = prop.get('height_above_start_cm')
    return (type(stamp) in (int,float) and math.isfinite(stamp) and 0 <= now-stamp <= 2
            and type(height) in (int,float) and math.isfinite(height) and height < -20.
            and prop.get('physics_simulated') is True and prop.get('gravity_enabled') is True
            and prop.get('attached_to') is None)


def lift_verdict(table_status, target, hold_seconds=2.0, max_tilt_deg=None):
    """Numbers only. Never a grasp claim: video review is still required."""
    p = (table_status or {}).get('props', {}).get(target)
    if not p:
        return {'measured': False}
    timer = p.get('current_slow_above_10cm_seconds', 0.) if max_tilt_deg is None else p.get('current_slow_upright_above_10cm_seconds', 0.)
    upright = max_tilt_deg is None or (p.get('upright_threshold_deg') == max_tilt_deg and 0 <= p.get('tilt_degrees', 180) <= max_tilt_deg)
    return {'measured': True, 'tilt_degrees': p.get('tilt_degrees'), 'upright_hold_s': timer if max_tilt_deg is not None else None,
            'height_cm': p.get('height_above_start_cm', 0.),
            'max_height_cm': p.get('max_z_cm', 0.) - p.get('initial_z_cm', 0.),
            'held_slow_above_10cm_s': p.get('current_slow_above_10cm_seconds', 0.),
            'max_held_slow_above_10cm_s': p.get('max_slow_above_10cm_seconds', 0.),
            'physics_simulated': p.get('physics_simulated'), 'attached_to': p.get('attached_to'),
            'gravity_enabled': p.get('gravity_enabled'),
            'lift_held': (timer >= hold_seconds and upright
                          and p.get('height_above_start_cm', 0.) >= 10
                          and p.get('gravity_enabled') is True
                          and p.get('physics_simulated') is True and not p.get('attached_to'))}


class Editor:
    def __init__(self, project):
        self.project = Path(project)
        self.lock = threading.Lock()

    def tools_path(self):
        """Editor Python may start without the project's Tools folder (e.g. after a restart)."""
        return 'import sys\nsys.path.insert(0, %r) if %r not in sys.path else None\n' % ((str(self.project/'Tools'),)*2)

    def call(self, tool, args, timeout=60):
        if tool == 'run_python':
            args = dict(args, code=self.tools_path()+args['code'])
        env = dict(os.environ, COPRESENCE_UNREAL_MCP_URL='http://127.0.0.1:8041/mcp')
        env.pop('DREAM_API_KEY', None)
        with self.lock:
            result = subprocess.run(['uv', 'run', '--script', 'Tools/copresence_mcp.py', 'call', tool, json.dumps(args)],
                                    cwd=self.project, env=env, capture_output=True, text=True, timeout=timeout)
        if result.returncode or 'ERROR' in result.stdout or 'rejected' in result.stdout.lower():
            raise RuntimeError((result.stdout+result.stderr)[-1500:])
        return result.stdout

    RELOAD = ('import importlib, maker_arm_fleet_layout, maker_arm_fleet\n'
              'importlib.reload(maker_arm_fleet_layout); importlib.reload(maker_arm_fleet)\n')

    def scene(self, expression, description, timeout=180):
        """Shared, replicated edit: one CoPresence run_python transaction."""
        out = self.call('run_python', {'code': self.RELOAD + 'result = ' + expression,
                                       'description': description}, timeout=timeout)
        if 'Traceback' in out:
            raise RuntimeError(out[-1500:])
        return out.strip()

    def deferred(self, function, args, result_path, timeout=90):
        """Run maker_arm_fleet_runtime.<function> on the next editor tick and wait for its result file."""
        result_path = Path(result_path)
        result_path.unlink(missing_ok=True)
        code = ('import importlib, maker_arm_fleet_layout, maker_arm_fleet, maker_arm_fleet_runtime\n'
                'importlib.reload(maker_arm_fleet_layout); importlib.reload(maker_arm_fleet)\n'
                'runtime = maker_arm_fleet_runtime if maker_arm_fleet_runtime._session and maker_arm_fleet_runtime._session.callback '
                'else importlib.reload(maker_arm_fleet_runtime)\n'
                'result = runtime.deferred(%r, %r, %r)' % (function, list(args), str(result_path)))
        self.call('run_python', {'code': code, 'description': 'Fleet: ' + function.replace('_', ' ')})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = read_json(result_path)
            if value is not None:
                if not value.get('ok'):
                    raise RuntimeError('%s failed in editor: %s' % (function, value.get('error')))
                return value['result']
            time.sleep(.2)
        raise TimeoutError(function + ' did not report back from the editor')


class TableRun:
    def __init__(self, variant, run, args, client=None):
        self.v = variant
        self.key = 'T%02d' % variant['index']
        self.dir = run/self.key
        self.run = run
        self.args = args
        self.client = client
        self.target_fell = False
        self.grip_latch = None  # opt-in assist: max grip command once jaws stall on the cup
        self.latch_z = None  # tool height (m, arm frame) when the jaws first stalled
        self.hold_peak_z = None  # highest tool height while holding (hold_monotonic floor)
        self.cup_rise = 0.  # latest measured target rise (cm), for phase prompts
        self.grasp_cycles = 0  # grasp_still cycles used before the jaws hold the cup
        self.regrasps = 0  # re-reaches after the cup dropped back upright
        self.lift_stalls = 0  # consecutive lift cycles without measured rise
        self.last_rise = 0.
        self.result = {'table': self.key, 'name': variant['name'], 'prompt': variant['prompt'],
                       'checkpoint': variant['checkpoint'], 'position_ik': variant['position_ik'],
                       'pad_contacts': variant['pad_contacts'], 'action_rows': variant['action_rows'],
                       'cycles_planned': variant['cycles'], 'cycles_run': 0, 'calls': 0,
                       'lift': {'measured': False}, 'error': None, 'stopped_reason': None, 'events': [],
                       'requests': [], 'motion_chunks': [], 'assist': variant.get('assist') or {}}

    def log(self, message, **extra):
        item = {'t': round(time.time(), 3), 'message': message, **extra}
        self.result['events'].append(item)
        print('[%s] %s' % (self.key, message), flush=True)

    def stopped(self):
        return (self.run/'stop').exists()

    def status(self):
        fleet = read_json(self.run/'fleet.json') or {}
        if fleet.get('phase') in ('error', 'stopped'):
            raise RuntimeError('Fleet runtime %s: %s' % (fleet.get('phase'), fleet.get('error')))
        table = fleet.get('tables', {}).get(self.key)
        return {**table, 'sampled_unix': fleet.get('sampled_unix')} if table is not None else None

    def fresh(self, after):
        started = time.monotonic()
        deadline = started + 15
        asked = False
        while time.monotonic() < deadline:
            if not asked and time.monotonic() - started > 2:
                (self.dir/'need_observation').touch()  # runtime captures on demand
                asked = True
            if self.stopped():
                raise RuntimeError('Stopped')
            table = self.status()
            if table and table.get('error'):
                raise RuntimeError(table['error'])
            packet = read_json(self.dir/'observation.json')
            if packet and packet['captured_unix'] > after and not packet.get('moving') and time.time()-packet['captured_unix'] < 3:
                return packet
            time.sleep(.1)
        raise TimeoutError('No fresh verified observation for ' + self.key)

    def goal_status(self, lift=None):
        """Live goal summary for the web panel: target height, measured rise, phase."""
        objective = self.v.get('objective') or {}
        if not objective:
            return None
        held = self.result.get('threshold_hold') or {}
        rise = float((lift or {}).get('height_cm', self.cup_rise) or 0.)
        phase = ('reach' if self.result['cycles_run'] == 0 and self.v.get('assist', {}).get('approach') == 'scripted'
                 else 'grasp' if self.grip_latch is None else
                 'lift' if rise < float(objective.get('height_cm', 10.))-.5 else 'hold')
        return {'required_height_cm': objective.get('height_cm', 10.), 'hold_seconds': objective.get('hold_seconds', 5.),
                'rise_cm': round(rise, 1), 'phase': phase, 'held_s': held.get('held_s', 0.),
                'achieved': bool(held.get('passed') or (lift or {}).get('lift_held'))}

    def prompt_now(self):
        """Opt-in language subgoals: grasp until the jaws hold the cup, lift until the
        table's height is reached, then hold. Dream produces every action for each."""
        phases = self.v.get('assist', {}).get('phase_prompts')
        if not phases:
            return self.v['prompt']
        if self.grip_latch is None:
            return phases.get('grasp') or self.v['prompt']
        required = float((self.v.get('objective') or {}).get('height_cm', 10.))
        if self.cup_rise >= required-.5:
            return phases['hold']
        options = phases.get('lift_variants') or [phases['lift']]
        return options[self.lift_stalls % len(options)]

    def infer(self, packet):
        if self.args.dry_run:
            return hold_action(packet['state'], self.v['action_rows'] or 12), None
        from dream.errors import DreamSessionExpired
        from session_recovery import request_action
        holder = {'packet': packet}

        def refresh():
            if holder.get('used') or time.time()-holder['packet']['captured_unix'] >= 2:
                holder['packet'] = self.fresh(time.time())
            holder['used'] = True
            p = holder['packet']
            obs = {'prompt': self.prompt_now()}
            for side in ('left', 'right'):
                obs['state.'+side+'_arm'] = p['state']['q_'+side]
                obs['state.'+side+'_grip'] = [p['state']['gap_'+side+'_m']/JAW_MAX_M]
            self.client.set_observation(p['frames'], obs, timestamp=p['captured_monotonic'])
            return p

        def call():
            self.result['calls'] += 1
            request = {'call': self.result['calls'], 'started_unix': time.time(),
                       'observation_unix': holder['packet']['captured_unix'], 'prompt': self.prompt_now()}
            self.result['requests'].append(request)
            try:
                action = self.client.get_action()
                request['returned_action'] = True
                return action
            except Exception as error:
                request['error'] = str(error)
                raise
            finally:
                request['finished_unix'] = time.time()
                write_json(self.dir/'requests.json', self.result['requests'])
        capacity_deadline = time.monotonic() + 60
        for attempt in range(9):
            while not _INFERENCE_SLOTS.acquire(timeout=.2):
                if self.stopped():
                    raise RuntimeError('Stopped waiting for Dream capacity')
            try:
                return request_action(call, refresh, self.stopped, lambda m: self.log(m), DreamSessionExpired)
            except Exception as error:
                numerical = 'SVD did not converge' in str(error)
                if (('NO_CAPACITY' not in str(error) and not numerical) or attempt == 8
                        or time.monotonic() >= capacity_deadline):
                    raise
                self.log(('Dream client IK numerical failure; retry %d/8' if numerical else
                          'Dream capacity busy; bounded retry %d/8 (60 s budget)') % (attempt+1))
            finally:
                _INFERENCE_SLOTS.release()
            deadline = min(capacity_deadline, time.monotonic() + min(8, 2**(attempt+1)) + self.v['index']*.1)
            while time.monotonic() < deadline:
                if self.stopped():
                    raise RuntimeError('Stopped during Dream capacity backoff')
                time.sleep(.1)

    def execute(self):
        objective = self.v.get('objective') or {}
        hold_seconds = float(objective.get('hold_seconds', 5.))
        max_tilt = objective.get('max_tilt_deg')
        after = time.time() - 1
        target = self.v['target']['label']
        from action_prefix_guard import PrefixGuard
        prefix_guard = PrefixGuard()
        reach_pending = self.v.get('assist', {}).get('approach') == 'scripted' and not self.args.dry_run
        for cycle in range(self.v['cycles']+int(reach_pending)):
            if self.stopped():
                self.result['stopped_reason'] = 'fleet stopped'
                return
            if objective and target_cup_fell(self.status(), target):
                self.target_fell = True
                self.result['stopped_reason'] = 'target cup fell more than 20 cm below its starting height'
                self.log(self.result['stopped_reason'])
                return
            packet = self.fresh(after)
            started = time.monotonic()
            reaching, reach = reach_pending, None
            if reaching:
                from pregrasp import approach_action
                reach_pending = False
                action, reach = approach_action(packet['state'], self.v)
                used = None
                self.log('planned reach from idle to the cup (%d rows, no Dream call)' % reach['rows'])
            else:
                action, used = self.infer(packet)
            packet = used or packet
            raw, ik = action, None
            write_json(self.dir/('raw-action-%02d.json' % (cycle+1)),
                       {'state': packet['state'], 'action': raw, 'captured_unix': packet['captured_unix']})
            active_side = self.v.get('motion_side')
            if self.v['position_ik'] and not self.args.dry_run and not reaching:
                from position_ik import adapt
                active = [active_side] if active_side else None
                try:
                    action, ik = adapt(raw, packet['state'], self.v['action_rows'] or 12, active_sides=active, allow_prefix=True)
                except np.linalg.LinAlgError as error:
                    # Numerical failure inside the solver: hold this cycle and replan.
                    self.log('IK numerical failure (%s); holding pose this cycle' % error)
                    action, ik = hold_action(packet['state'], 2), {'_execution': {'truncated': False, 'linalg_error': str(error)}}
            if active_side and not self.args.dry_run:
                action = constrain_motion_side(action, packet['state'], active_side)
            guard = None
            if self.v.get('assist', {}).get('table_guard') and not self.args.dry_run:
                from pregrasp import guard_table
                guard = []
                for side in ([active_side] if active_side else ['left', 'right']):
                    action, report = guard_table(action, packet['state'], side)
                    guard.append(report)
                raised = sum(len(g['raised_rows']) for g in guard)
                if raised:
                    self.log('table guard raised %d row(s) to keep pads above the tabletop' % raised)
            latch_side = active_side or 'left'
            latch_key = 'q_l_chunk' if latch_side == 'left' else 'q_r_chunk'
            if (self.v.get('assist', {}).get('grasp_still') and self.grip_latch is None and not reaching
                    and not self.args.dry_run and self.grasp_cycles < 3):
                # Grasp subgoal: the jaws are already around the cup after the reach. Keep
                # the arm still so closing does not push the cup away; Dream drives the grip.
                self.grasp_cycles += 1
                still = list(packet['state']['q_'+latch_side])
                action = dict(action)
                action[latch_key] = [still+[row[6]] for row in action[latch_key]]
                self.log('grasp: arm held still, Dream grip %.2f -> %.2f' % (action[latch_key][0][6], action[latch_key][-1][6]))
            vertical = None
            if self.v.get('assist', {}).get('hold_vertical') and self.grip_latch is not None and not self.args.dry_run:
                from pregrasp import vertical_only
                floor = self.hold_peak_z if self.v['assist'].get('hold_monotonic') else self.latch_z
                assist = self.v['assist']
                action, vertical = vertical_only(action, packet['state'], latch_side, floor_z=floor,
                                                 max_step_cm=float(assist.get('lift_step_cm', .25)),
                                                 back_per_up=float(assist.get('lift_back_per_up', 0.)))
                self.log('holding: applied only vertical motion (dz %+.1f cm over %d rows)' %
                         ((vertical['dz_cm'] or [0])[-1], vertical['rows']))
            if self.v.get('assist', {}).get('grip_latch') and self.grip_latch is not None and not self.args.dry_run:
                clamped = sum(1 for row in action[latch_key] if abs(row[6]-self.grip_latch) > 1e-3)
                action = dict(action)
                action[latch_key] = [list(row[:6])+[self.grip_latch] for row in action[latch_key]]
                if clamped:
                    self.log('grip latch kept %d row(s) closed at %.2f' % (clamped, self.grip_latch))
            execution = (ik or {}).get('_execution') or {}
            if execution.get('truncated'):
                self.log('Executing feasible policy prefix %s/%s rows; replan from fresh feedback' %
                         (execution.get('executed_rows'), execution.get('requested_rows')))
            rows_to_play = None if self.args.dry_run else execution.get('executed_rows', self.v['action_rows'])
            if (reaching or vertical) and not self.args.dry_run:
                # The planned reach and split held lifts are complete trajectories; the
                # policy horizon (action_rows) must not truncate them.
                rows_to_play = len(action[latch_key])
            report = self.dir/('action-%02d.json' % (cycle+1))
            write_json(report, {'state': packet['state'], 'action': action, 'raw_action': raw if ik else None,
                                'ik_report': ik, 'table_guard': guard, 'vertical_hold': vertical, 'grip_latch': self.grip_latch, 'motion_side': active_side, 'execution_rows': rows_to_play,
                                'checkpoint': None if self.args.dry_run else self.v['checkpoint'],
                                'reach': reach,
                                'controller_source': 'dry-run hold' if self.args.dry_run else
                                ('planned reach from idle (no Dream call)' if reaching else
                                 'Dream EEF + position IK' if ik else 'Dream joint targets')})
            inference = time.monotonic() - started
            self.status()  # Runtime may have reached its deadline during IK.
            write_json(self.dir/'command.json', {'seq': cycle+1, 'report': str(report)})
            deadline = time.monotonic() + 30
            while True:
                if self.stopped():
                    raise RuntimeError('Stopped during motion')
                self.status()
                playback = read_json(self.dir/'playback.json')
                if playback and playback.get('seq') == cycle+1 and playback.get('done'):
                    if playback.get('error'):
                        raise RuntimeError('Cycle %d rejected: %s' % (cycle+1, playback['error']))
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError('Cycle %d motion did not finish' % (cycle+1))
                time.sleep(.1)
            if self.v.get('assist', {}).get('grip_latch') and not self.args.dry_run:
                commanded = float(action[latch_key][-1][6])
                observed = playback.get('gap_'+latch_side+'_m')
                if observed is not None and observed - commanded*JAW_MAX_M > .01 and observed < .095:
                    if self.grip_latch is None:
                        from pregrasp import tool_height
                        self.latch_z = tool_height(playback.get('q_'+latch_side, packet['state']['q_'+latch_side]))
                    if self.grip_latch is None:
                        # The runtime limiter stops the pads at contact width minus its 5 mm
                        # compliance while both pads touch; if contact drops for a tick the jaws
                        # snap to the command. Command just past the compliance (6 mm) so the
                        # squeeze is unchanged but a lost contact cannot crush through the cup.
                        self.grip_latch = max(observed-float(self.v['assist'].get('grip_squeeze_m', .006)), 0.)/JAW_MAX_M
                        self.log('jaws stalled on an object at %.1f cm (commanded %.1f cm); holding %.1f cm' %
                                 (observed*100, commanded*JAW_MAX_M*100, self.grip_latch*JAW_MAX_M*100))
            if (self.grip_latch is not None and self.v.get('assist', {}).get('regrasp') and not reaching
                    and self.regrasps < int(self.v['assist'].get('regrasp', 0))):
                gap_now = playback.get('gap_'+latch_side+'_m', 1.)
                cup = (self.status() or {}).get('props', {}).get(target, {})
                if (gap_now <= self.grip_latch*JAW_MAX_M+.002 and cup.get('height_above_start_cm', 0.) < 2.
                        and 0 <= cup.get('tilt_degrees', 180.) <= 15 and cup.get('table_local_cm')):
                    # Jaws closed to the latch command: the cup dropped back onto the table upright.
                    # Re-reach to where it is now and grasp again (counted, labelled).
                    self.regrasps += 1
                    self.v = {**self.v, 'target': {**self.v['target'], 'location_cm': list(cup['table_local_cm'][:2])+[81.]}}
                    self.grip_latch = self.latch_z = self.hold_peak_z = None
                    self.grasp_cycles = self.lift_stalls = 0
                    self.last_rise = 0.
                    reach_pending = True
                    self.log('cup dropped upright at %s; re-reaching (regrasp %d)' %
                             ([round(v, 1) for v in cup['table_local_cm'][:2]], self.regrasps))
            if self.latch_z is not None and playback.get('q_'+latch_side):
                from pregrasp import tool_height
                self.hold_peak_z = max(self.hold_peak_z or self.latch_z, tool_height(playback['q_'+latch_side]))
            motion_evidence = dict(playback)
            motion_evidence['max_joint_delta_rad'] = max(abs(a-b)
                for side in ('left', 'right')
                for a, b in zip(packet['state']['q_'+side], playback.get('q_'+side, packet['state']['q_'+side])))
            self.result['motion_chunks'].append(motion_evidence)
            after = playback.get('finished_unix') or time.time()
            self.result['cycles_run'] = cycle+1
            current=self.status()
            metrics=cup_metrics(current)
            self.result['cup_metrics']=metrics
            monitored=target if objective else cup_candidate(metrics)
            self.result['measured_cup']=monitored
            lift = lift_verdict(current, target, hold_seconds, max_tilt) if objective else metrics.get(monitored, {'measured':False})
            self.result['lift'] = lift
            self.cup_rise = float(lift.get('height_cm', 0.) or 0.)
            if self.grip_latch is not None:
                self.lift_stalls = self.lift_stalls+1 if self.cup_rise < self.last_rise+.5 else 0
            self.last_rise = max(self.last_rise, self.cup_rise)
            self.log('cycle %d done (inference %.2fs) lift %.2f cm' % (cycle+1, inference, lift.get('height_cm', 0.)))
            gap_delta = max(abs(playback.get('gap_'+side+'_m', packet['state']['gap_'+side+'_m'])-
                                packet['state']['gap_'+side+'_m']) for side in ('left','right'))
            bounded_stop = prefix_guard.observe(execution, motion_evidence['max_joint_delta_rad'], gap_delta)
            if bounded_stop:
                self.result['error'] = bounded_stop
                self.result['stopped_reason'] = bounded_stop
                self.log(bounded_stop)
                return
            required = float(objective.get('height_cm', 10.)) if objective else 10.
            if objective and required != 10 and lift.get('height_cm', 0) >= required:
                self.log('target above %.0f cm; holding pose to measure a continuous %.1f s hold' % (required, hold_seconds))
                held = self.sampled_hold(target, required, hold_seconds, max_tilt)
                self.result['threshold_hold'] = held
                self.result['lift'] = {**lift_verdict(self.status(), target, hold_seconds, max_tilt), 'lift_held': held['passed'],
                                       'required_height_cm': required, 'held_above_required_s': held['held_s']}
                if held['passed']:
                    self.result['stopped_reason'] = 'measured %.0f cm hold for %.1f s (needs video review)' % (required, held['held_s'])
                    self.log(self.result['stopped_reason'])
                    return
                continue
            if lift.get('height_cm', 0) >= 10 and required == 10:
                self.log('target above 10 cm; holding pose for %.1f s to measure retention' % (hold_seconds+.2))
                hold_until = time.monotonic()+hold_seconds+.2
                while time.monotonic() < hold_until:
                    if self.stopped():
                        raise RuntimeError('Stopped during hold verification')
                    self.status()
                    time.sleep(.1)
                current=self.status()
                metrics=cup_metrics(current)
                self.result['cup_metrics']=metrics
                # Hold the SAME object label through this retention window.
                lift = lift_verdict(current,target,hold_seconds,max_tilt) if objective else metrics.get(monitored,{'measured':False})
                self.result['lift'] = lift
                if lift.get('lift_held'):
                    self.result['stopped_reason'] = 'measured held lift (needs video review)'
                    self.log('measured held lift %.2f cm for %.2f s' % (lift['height_cm'], lift['held_slow_above_10cm_s']))
                    return
        self.result['stopped_reason'] = 'cycles exhausted'

    def sampled_hold(self, target, height, hold_seconds, max_tilt):
        """Continuous hold above an arbitrary height from ~10 Hz runtime samples; numbers only."""
        start, best, samples = None, 0., []
        until = time.monotonic()+hold_seconds+.8
        while time.monotonic() < until:
            if self.stopped():
                raise RuntimeError('Stopped during hold verification')
            p = (self.status() or {}).get('props', {}).get(target, {})
            h, tilt, speed = p.get('height_above_start_cm', 0.), p.get('tilt_degrees', 180.), p.get('speed_cm_s', 99.)
            ok = (h >= height and speed <= 10 and (max_tilt is None or 0 <= tilt <= max_tilt)
                  and p.get('physics_simulated') is True and p.get('gravity_enabled') is True and not p.get('attached_to'))
            now = time.monotonic()
            start = (start or now) if ok else None
            best = max(best, now-start) if start else best
            samples.append([round(h, 2), round(tilt, 1), round(speed, 1)])
            if best >= hold_seconds:
                break
            time.sleep(.1)
        heights = [x[0] for x in samples] or [0.]
        return {'required_height_cm': height, 'required_hold_s': hold_seconds, 'max_tilt_deg': max_tilt,
                'held_s': round(best, 2), 'passed': best >= hold_seconds, 'min_height_cm': min(heights),
                'max_height_cm': max(heights), 'samples': len(samples),
                'scope': 'cup centre rise sampled from runtime physics; not grasp proof'}

    def __call__(self):
        self.finished = False
        try:
            self.execute()
        except Exception as error:
            self.result['error'] = str(error)
            self.log('error: ' + str(error))
        finally:
            if self.client is not None and not self.args.dry_run:
                try:
                    self.client.reset_episode()
                    self.result['policy_session_released'] = True
                except Exception as error:
                    self.result['policy_session_release_error'] = str(error)
        from pickup_verification import verify_pickup
        fleet = read_json(self.run/'fleet.json') or {}
        status = {**fleet.get('tables', {}).get(self.key, {}), 'sampled_unix': fleet.get('sampled_unix')}
        verification = verify_pickup(self.v.get('objective'), self.v['target']['label'], status,
                                     requests=self.result['requests'], motion_chunks=self.result['motion_chunks'])
        self.result['verification'] = verification
        self.result['cup_metrics']=cup_metrics(status)
        if self.args.dry_run:
            self.result['outcome'] = 'dry_run'
        elif self.target_fell:
            self.result['outcome'] = 'failed'
        elif self.stopped():
            self.result['outcome'] = 'stopped'
        elif self.result['error']:
            self.result['outcome'] = 'error'
        elif not self.v.get('objective'):
            self.result['outcome'] = 'unverified'
        else:
            self.result['outcome'] = verification['outcome']
            held = self.result.get('threshold_hold')
            if held is not None:
                self.result['outcome'] = 'passed' if held['passed'] else 'failed'
            elif float(self.v['objective'].get('height_cm', 10)) != 10:
                self.result['outcome'] = 'failed'
        self.result['objective'] = self.v.get('objective')
        self.finished = True
        return self.result


def concurrency_evidence(results):
    """Measured client request overlap, not an inference about server scheduling."""
    events = []
    for result in results:
        for request in result.get('requests', []):
            if request.get('finished_unix') is not None:
                events.extend([(request['started_unix'], 1, result['table']),
                               (request['finished_unix'], -1, result['table'])])
    active, peak, peak_tables = set(), 0, []
    for stamp, change, table in sorted(events):
        if change < 0:
            active.discard(table)
        else:
            active.add(table)
        if len(active) > peak:
            peak, peak_tables = len(active), sorted(active)
    return {'peak_concurrent_request_tables': peak, 'peak_tables': peak_tables,
            'tables_with_calls': sorted(r['table'] for r in results if r.get('calls', 0) > 0),
            'tables_with_measured_joint_change': sorted(r['table'] for r in results
                if any(c.get('max_joint_delta_rad', 0) > 1e-4 for c in r.get('motion_chunks', []))),
            'tables_with_motion_rows': sorted(r['table'] for r in results
                if any(c.get('rows', 0) > 1 and not c.get('error') for c in r.get('motion_chunks', []))),
            'note': 'Overlap measures outstanding Dream client calls; motion rows are playback evidence, not task success.'}


def make_client(variant, run):
    from dream import DreamClient
    from model import prepare
    from probe import configure
    with _SETUP_LOCK:
        arm = prepare(run/('model-tcp%03d' % round(variant['tcp_back']*1000)), tcp_back=variant['tcp_back'])
        client = DreamClient()
        try:
            checkpoint = STANDARD if variant['checkpoint'] == 'auto' else variant['checkpoint']
            configure(client, arm, checkpoint)
        except Exception:
            client.close()
            raise
    try:
        if not client.wait_for_ik(240):
            raise TimeoutError('IK initialization timed out for table %d' % variant['index'])
    except Exception:
        client.close()
        raise
    return client


def videos(run, key, fps):
    """Overhead review video from the moving-table frames; sparse wrist clips from policy packets."""
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        ffmpeg = shutil.which('ffmpeg')
    out = {}
    if not ffmpeg:
        return out
    folder = run/key/'recording'
    jobs = [('overhead', 'overhead_rec_%06d.jpg' if (folder/'overhead_rec_000001.jpg').exists() else 'overhead_%06d.jpg',
             fps if (folder/'overhead_rec_000001.jpg').exists() else 1),
            ('wrist_l', 'wrist_l_%06d.jpg', 1), ('wrist_r', 'wrist_r_%06d.jpg', 1)]
    for role, pattern, rate in jobs:
        if not (folder/(pattern % 1)).exists():
            continue
        target = run/key/(role+'.mp4')
        from recording_timeline import write_concat
        timeline = write_concat(folder, fps) if role == 'overhead' else None
        source = (['-f', 'concat', '-safe', '1', '-i', str(timeline), '-fps_mode', 'vfr'] if timeline else
                  ['-framerate', str(rate), '-i', str(folder/pattern)])
        done = subprocess.run([ffmpeg, '-y', '-loglevel', 'error', *source,
                               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(target)], capture_output=True, timeout=60)
        if done.returncode == 0:
            out[role] = str(target)
    return out


def latest_overhead(run, key):
    """Newest complete overhead JPEG for a live thumbnail (review frame or policy packet)."""
    best = (None, None)
    frames = sorted((run/key/'recording').glob('overhead_rec_*.jpg'))[-2:]
    for path in reversed(frames):
        data = path.read_bytes()
        if data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
            best = (data, path.stat().st_mtime)
            break
    packet = read_json(run/key/'observation.json')
    if packet and packet.get('frames', {}).get('video.overhead') and (best[1] is None or packet['captured_unix'] > best[1]):
        import base64
        return base64.b64decode(packet['frames']['video.overhead']), packet['captured_unix']
    return best


def print_table(results):
    print('\n%-4s %-28s %6s %7s %7s %s' % ('tbl', 'variant', 'cycles', 'lift', 'held', 'result'), flush=True)
    for r in results:
        lift = r['lift']
        print('%-4s %-28s %6d %6.2fc %6.2fs %s' % (r['table'], r['name'][:28], r['cycles_run'], lift.get('max_height_cm', 0.),
                                                   lift.get('max_held_slow_above_10cm_s', 0.),
                                                   'HELD LIFT (review video)' if lift.get('lift_held') else (r['error'] or r['stopped_reason'])), flush=True)


def new_run(data, config):
    run = data/(time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6])
    run.mkdir(parents=True)
    (run/'config.json').write_text(json.dumps(config, indent=2))
    print('Fleet run directory:', run, flush=True)
    return run


def run_trials(args, editor, variants, clients, run, progress=None, should_stop=None, title=None, after_cleanup=None):
    """Hold the editor task, reset start poses, run every table in one SIE session.

    progress(fleet_json, runs) is called about four times a second; should_stop() is
    polled and stops the whole fleet. Returns the per-table results (best first)."""
    config_path = run/'config.json'
    # No table inference is active here. Release prior episodes without rebuilding CAD IK.
    for client in clients.values():
        client.reset_episode()
    title = title or 'Fleet: %d parallel %s trials' % (len(variants), 'dry-run' if args.dry_run else 'Dream')
    wait_for_editor(lambda: editor.call('begin_task', {'title': title, 'request': 'Replica Maker Arm tables in the open-top grid; parallel simulation',
                                                       'keep_view': True, 'focus_x': 40, 'focus_y': 0, 'focus_z': 100}),
                    lambda: bool(should_stop and should_stop()), lambda m: print(m, flush=True), timeout=900)
    results, note, runs = [], '', []
    done = threading.Event()

    refreshed = {}

    def monitor():
        while not done.wait(.25):
            try:
                if should_stop and should_stop():
                    (run/'stop').touch()
                # Keep live thumbnails for tables that already finished: the runtime
                # captures on demand, so ask for an idle observation every 2 s.
                for r in runs:
                    if getattr(r, 'finished', False) and time.monotonic()-refreshed.get(r.key, 0) > 2:
                        (r.dir/'need_observation').touch()
                        refreshed[r.key] = time.monotonic()
                if progress:
                    progress(read_json(run/'fleet.json') or {}, runs)
            except Exception as error:
                print('progress update failed: %s' % error, flush=True)
    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    try:
        for v in variants:
            editor.scene('maker_arm_fleet.build_table(%r, %d)' % (str(config_path), v['index']),
                         'Fleet: booth T%02d (%s)' % (v['index'], v['name']))
        if any(v.get('assist', {}).get('layout') == 'live' for v in variants):
            from pregrasp import live_cups_expression, apply_live_layout
            editor.scene(live_cups_expression(run/'live-cups.json'), 'Fleet: read current cup layout (no edits)')
            live = read_json(run/'live-cups.json')
            if not live:
                raise RuntimeError('Editor did not report live cup positions')
            write_json(config_path, apply_live_layout(read_json(config_path), live))
            fresh = {v['index']: v for v in normalize_config(read_json(config_path))}
            variants = [fresh.get(v['index'], v) for v in variants]
            print('Live cup layout applied to %d table(s)' % sum(v.get('assist', {}).get('layout') == 'live' for v in variants), flush=True)
        out = editor.scene('maker_arm_fleet.reset(%r)' % str(config_path), 'Fleet: set start poses for this run')
        print('Start poses: ' + out.splitlines()[-1][:300], flush=True)
        editor.deferred('start', [str(config_path), str(run), args.fps, args.max_seconds, 1], run/'start-result.json')
        deadline = time.monotonic() + 40
        while (read_json(run/'fleet.json') or {}).get('phase') != 'ready':
            status = read_json(run/'fleet.json') or {}
            if status.get('phase') in ('error', 'stopped') or time.monotonic() > deadline:
                raise RuntimeError('Fleet runtime did not become ready: %s' % status.get('error'))
            time.sleep(.2)
        print('SIE running with %d tables' % len(variants), flush=True)
        runs.extend(TableRun(v, run, args, clients.get(v['index'])) for v in variants)
        with concurrent.futures.ThreadPoolExecutor(len(runs)) as pool:
            results = list(pool.map(lambda r: r(), runs))
        note = '%d tables finished.' % len(results)
    finally:
        if (run/'fleet.json').exists():
            (run/'stop').touch()
            deadline = time.monotonic() + 40
            while (read_json(run/'fleet.json') or {}).get('phase') not in ('stopped', 'error') and time.monotonic() < deadline:
                time.sleep(.2)
        done.set()
        try:
            editor.call('end_task', {'summary': 'Replica-table run: ' + (note or 'stopped early.') +
                                     ' Authoritative physics visual poses were mirrored at 2 Hz; editor start poses restored. Measured lift requires video review.',
                                     'keep_view': True})
        except Exception as error:
            print('end_task failed: %s' % error, flush=True)
        else:
            if after_cleanup:
                try: after_cleanup()
                except Exception as error: print('Preview handoff failed: '+str(error), flush=True)
    final_fleet = read_json(run/'fleet.json') or {}
    for result in results:
        final_table = final_fleet.get('tables', {}).get(result['table'], {})
        target = result.get('objective', {}).get('target') if result.get('objective') else None
        objective = result.get('objective') or {}
        result['batch_end_cup_metrics']=cup_metrics({**final_table,'sampled_unix':final_fleet.get('sampled_unix')})
        result['batch_end_lift'] = lift_verdict(final_table, target, objective.get('hold_seconds', 2.), objective.get('max_tilt_deg'))
        result['videos'] = videos(run, result['table'], args.fps)
    results.sort(key=lambda r: (not r['lift'].get('lift_held', False), -r['lift'].get('max_height_cm', 0.)))
    write_json(run/'summary.json', {'run': str(run), 'dry_run': args.dry_run, 'results': results, 'concurrency': concurrency_evidence(results),
                                    'note': 'Measured numbers only; confirm a pickup in the recorded video.'})
    return results


def warm_clients(variants, run, errors=None):
    clients = {}
    errors = errors if errors is not None else {}
    print('Configuring %d Dream sessions (custom CAD IK warm-up)...' % len(variants), flush=True)
    with concurrent.futures.ThreadPoolExecutor(len(variants)) as pool:
        futures = {pool.submit(make_client, v, run): v['index'] for v in variants}
        for future in concurrent.futures.as_completed(futures):
            try:
                clients[futures[future]] = future.result()
            except Exception as error:
                errors[futures[future]] = str(error)
                print('Table %d Dream setup failed: %s' % (futures[future], error), flush=True)
    return clients


_WORKER_LOCK = None

def acquire_worker_lock(data):
    """One authoritative fleet worker per project, across every checkout."""
    import fcntl
    global _WORKER_LOCK
    stream = (Path(data)/'worker.lock').open('a+')
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        raise RuntimeError('Another fleet worker owns this project; refusing duplicate startup')
    stream.seek(0)
    stream.truncate()
    stream.write(json.dumps({'pid': os.getpid(), 'checkout': str(REPO), 'started_unix': time.time()}))
    stream.flush()
    _WORKER_LOCK = stream  # OS releases on process exit, no stale-PID deletion.


class Server:
    """Bearer-authenticated client for the Luna2 Dream debugger's /api/fleet routes."""
    def __init__(self, url, token_file):
        self.url = url.rstrip('/')
        self.worker_id = uuid.uuid4().hex
        self.token = Path(token_file).read_text().strip()

    def api(self, path, data=None, timeout=10):
        import urllib.request
        if path.startswith('/api/fleet/worker/'):
            if data is None:
                path += ('&' if '?' in path else '?') + 'worker_id=' + self.worker_id
            else:
                data = {**data, 'worker_id': self.worker_id}
        body = None if data is None else json.dumps(data, default=lambda x: x.tolist() if hasattr(x, 'tolist') else str(x)).encode()
        request = urllib.request.Request(self.url+path, data=body, headers={'Authorization': 'Bearer '+self.token,
                                                                            'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)

    def event(self, kind, request_id, message, **extra):
        try:
            self.api('/api/events', {'kind': kind, 'request_id': request_id, 'message': message, **extra})
        except Exception as error:
            print('event delivery failed: %s' % error, flush=True)


def passive_preview_frames(run, now=None):
    """Read only complete camera files; never add passive tables to run results."""
    import base64
    now = time.time() if now is None else now
    packet = read_json(Path(run)/'passive-preview/frames.json') or {}
    frames, stamps = {}, {}
    for key, path in packet.get('frames', {}).items():
        stamp = packet.get('frame_times', {}).get(key)
        if key not in ['T%02d' % i for i in range(9)] or type(stamp) not in (int,float) or not 0 <= now-stamp <= 5:
            continue
        try: data = Path(path).read_bytes()
        except OSError: continue
        if len(data) <= 200_000 and data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
            frames[key], stamps[key] = base64.b64encode(data).decode(), stamp
    return frames, stamps


def table_progress(fleet, runs):
    out = {}
    tables = fleet.get('tables', {})
    for r in runs:
        status = {**tables.get(r.key, {}),'sampled_unix':fleet.get('sampled_unix')}
        objective = r.v.get('objective') or {}
        metrics=cup_metrics(status)
        selected=r.result.get('measured_cup') or cup_candidate(metrics)
        lift = lift_verdict(status,r.v['target']['label'],objective.get('hold_seconds',2.),objective.get('max_tilt_deg')) if objective else metrics.get(selected,{'measured':False})
        out[r.key] = {'calls': r.result['calls'], 'returned_actions': sum(bool(q.get('returned_action')) for q in r.result['requests']),
                      'cycles_run': r.result['cycles_run'], 'cycles_planned': r.result['cycles_planned'],
                      'cup_metrics':metrics,'measured_cup':selected,'lift': lift, 'error': r.result['error'] or status.get('error'),
                      'stopped_reason': r.result['stopped_reason'], 'moving': status.get('moving'),
                      'goal': r.goal_status(lift)}
    return out


class IdlePreview:
    def __init__(self, editor, directory, server):
        self.editor, self.directory, self.server = editor, Path(directory), server
        self.directory.mkdir(parents=True, exist_ok=True)
        self.running = False
        self.sent = {}
        self.retry_after = 0.
        self.last_sequence = None
        self.last_advance = time.monotonic()

    def set_enabled(self, enabled):
        if bool(enabled) == self.running or (enabled and time.monotonic() < self.retry_after):
            return
        # Local capture setup only; no shared geometry changes or long-held task.
        self.editor.call('begin_task', {'title': 'Fleet idle cameras', 'request': 'Update opt-in local table camera preview', 'keep_view': True, 'focus_x': 40, 'focus_y': 0, 'focus_z': 100})
        try:
            function = 'start_preview' if enabled else 'stop_preview'
            self.editor.deferred(function, [str(self.directory)] if enabled else [], self.directory/'control-result.json')
            self.running = bool(enabled)
            self.last_sequence = None
            self.last_advance = time.monotonic()
        finally:
            self.editor.call('end_task', {'summary': 'Local fleet camera preview updated; no robot motion.', 'keep_view': True})

    def publish(self):
        import base64
        packet = read_json(self.directory/'preview.json') or {}
        if packet.get('running') is False:
            self.running = False
            self.sent.clear()
        if packet.get('error'):
            self.retry_after = time.monotonic() + 10
            raise RuntimeError('Idle camera preview: ' + packet['error'])
        # Judge the UE callback by actual capture progress, not our cached flag.
        # A frozen/missing callback is restarted by the next idle worker cycle.
        sequence = packet.get('sequence')
        if sequence is not None and sequence != self.last_sequence:
            self.last_sequence = sequence
            self.last_advance = time.monotonic()
        elif self.running and time.monotonic()-self.last_advance > 10:
            self.running = False
            self.sent.clear()
            self.retry_after = time.monotonic()+1
            raise RuntimeError('Idle camera callback stalled; reconnecting')
        frames, stamps = {}, {}
        for key, path in packet.get('frames', {}).items():
            stamp = packet.get('frame_times', {}).get(key)
            if not stamp or stamp == self.sent.get(key):
                continue
            data = Path(path).read_bytes()
            if data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
                frames[key], stamps[key] = base64.b64encode(data).decode(), stamp
        interaction = frames.pop('InteractionT10', None)
        interaction_stamp = stamps.pop('InteractionT10', None)
        if frames:
            self.server.api('/api/fleet/worker/preview', {'frames': frames, 'frame_times': stamps})
            self.sent.update(stamps)
        if interaction is not None:
            self.server.api('/api/interaction/worker/preview',
                            {'frame': interaction, 'captured_unix': interaction_stamp})
            self.sent['InteractionT10'] = interaction_stamp


def poll_fleet_work(server, hello, maintenance=False):
    """Maintenance keeps camera heartbeat alive but never claims work/reset."""
    tables=[{**t,'ready':False,'error':'Maintenance: live cameras only; commands and resets disabled'} for t in hello] if maintenance else hello
    server.api('/api/fleet/worker/hello', {'tables':tables,'expected_tables':list(range(9)),
                                         'reset_supported':not maintenance})
    if maintenance:
        state=server.api('/api/fleet')
        return {'command':None,'reset':None,'preview_enabled':state.get('preview_enabled',True)}
    return server.api('/api/fleet/worker/next')


def serve(args, config):
    """Daemon: warm Dream sessions, then run whatever the web page queues."""
    import base64
    from fleet_planning import apply_plan
    server = Server(args.server, args.token_file)
    variants = normalize_config(config)
    data = args.data or args.project/'Saved/DreamFleet'
    data.mkdir(parents=True, exist_ok=True)
    acquire_worker_lock(data)
    previous_reset = read_json(data/'last-reset.json')
    if previous_reset is not None:
        from fleet_reset import reset_config
        config = reset_config(config, previous_reset['seed'])
        variants = normalize_config(config)
    setup_errors = {}
    clients = {} if args.dry_run else warm_clients(variants, data, setup_errors)
    import atexit, signal
    def close_clients():
        for client in clients.values():
            try:
                client.close()
            except Exception:
                pass
    atexit.register(close_clients)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    usable = [v for v in variants if args.dry_run or v['index'] in clients]
    warmed = {v['index']: v['checkpoint'] for v in variants}
    config_mtime = args.config.stat().st_mtime
    hello = [{**v, 'checkpoint': v['checkpoint'] if not args.dry_run else 'dry-run (no Dream calls)',
              'ready': args.dry_run or v['index'] in clients, 'error': setup_errors.get(v['index'])} for v in variants]
    editor = Editor(args.project)
    preview = IdlePreview(editor, data/'preview', server)
    seen = set()
    print('Fleet worker ready: %d tables%s' % (len(usable), ' (dry run)' if args.dry_run else ''), flush=True)
    while True:
        try:
            next_reply = poll_fleet_work(server,hello,getattr(args,'maintenance',False))
            command = next_reply['command']
        except Exception as error:
            print('debugger unreachable: %s' % error, flush=True)
            time.sleep(3)
            continue
        reset = next_reply.get('reset')
        if reset:
            error = None
            heartbeat_stop = threading.Event()
            def reset_heartbeat():
                while not heartbeat_stop.wait(.25):
                    try:
                        server.api('/api/fleet/worker/reset-progress', {'request_id': reset['id']})
                        if preview.running:
                            preview.publish()
                    except Exception as exc:
                        print('Reset heartbeat failed: '+str(exc), flush=True)
            heartbeat = threading.Thread(target=reset_heartbeat, daemon=True)
            heartbeat.start()
            try:
                from fleet_reset import reset_config, apply_idle_reset
                # Editor-only reset: keep the local capture callback alive. The
                # heartbeat publishes completed frames without any MCP calls.
                reset_scene = reset_config(config, reset['seed'])
                reset_dir = data/('reset-'+reset['id'])
                reset_dir.mkdir(parents=True, exist_ok=True)
                write_json(reset_dir/'config.json', reset_scene)
                apply_idle_reset(editor, reset_dir/'config.json')
                config = reset_scene
                write_json(data/'last-reset.json', {'seed': reset['seed'], 'request_id': reset['id']})
            except Exception as exc:
                error = str(exc)
                print('Reset failed: '+error, flush=True)
            finally:
                heartbeat_stop.set()
                heartbeat.join(timeout=11)
            server.api('/api/fleet/worker/reset-done', {'request_id': reset['id'], 'error': error})
            continue
        if not command or command['status'] != 'running' or command['id'] in seen:
            try:
                preview.set_enabled(next_reply.get('preview_enabled', False))
                if preview.running:
                    preview.publish()
            except Exception as error:
                print('idle preview unavailable: %s' % error, flush=True)
            time.sleep(.25)
            continue
        seen.add(command['id'])
        request_id = command['id']
        if getattr(args, 'reload_config', False) and args.config.stat().st_mtime != config_mtime:
            fresh = json.loads(args.config.read_text())
            changed = [v['index'] for v in normalize_config(fresh) if v['index'] in clients and v['checkpoint'] != warmed.get(v['index'])]
            if changed:
                print('Config reload refused: checkpoint changed for tables %s (restart the worker)' % changed, flush=True)
            else:
                last = read_json(data/'last-reset.json')
                if last is not None:
                    from fleet_reset import reset_config
                    fresh = reset_config(fresh, last['seed'])  # keep the current Reset layout
                config, config_mtime = fresh, args.config.stat().st_mtime
                print('Reloaded %s' % args.config, flush=True)
        chosen = [dict(t) for t in config['tables']]
        run_config = {'defaults': config.get('defaults', {}), 'tables': []}
        for position, table in enumerate(chosen):
            i = table.get('index', position)
            if i in command['tables'] and (args.dry_run or i in clients):
                table['index'] = i
                if command.get('prompt'):
                    table['prompt'] = command['prompt']
                run_config['tables'].append(table)
        if command.get('plan'):
            run_config = apply_plan(config, command['plan'])
        status = 'failed'
        stop = {'flag': False}
        publication_stop = threading.Event()
        publication_thread = None
        def restore_preview_after_cleanup():
            nonlocal publication_thread
            enabled = server.api('/api/fleet').get('preview_enabled', True)
            preview.set_enabled(enabled)
            def publish_during_encoding():
                while not publication_stop.wait(.25):
                    try:
                        if preview.running: preview.publish()
                    except Exception as error:
                        print('Post-run preview: '+str(error), flush=True)
            publication_thread = threading.Thread(target=publish_during_encoding, daemon=True)
            publication_thread.start()
        try:
            preview.set_enabled(False)
            missing = [i for i in command['tables'] if not args.dry_run and i not in clients]
            if missing:
                raise RuntimeError('Selected Dream sessions are unavailable: ' + ', '.join(map(str, missing)))
            if not run_config['tables']:
                raise RuntimeError('No selected table has a ready Dream session')
            from pregrasp import prepare_run_config
            run_config = prepare_run_config(run_config, config)
            run = new_run(data, run_config)
            run_variants = normalize_config(run_config)
            for v in run_variants:
                if v['index'] in clients:
                    clients[v['index']].reset_episode()
            server.event('fleet.started', request_id, 'Running ' + ' '.join('T%02d' % v['index'] for v in run_variants) + ' in parallel')

            def progress(fleet, runs):
                frames, frame_times = passive_preview_frames(run)
                for v in run_variants:
                    data, captured_at = latest_overhead(run, 'T%02d' % v['index'])
                    if data:
                        frames['T%02d' % v['index']] = base64.b64encode(data).decode()
                        frame_times['T%02d' % v['index']] = captured_at
                reply = server.api('/api/fleet/worker/status', {'request_id': request_id, 'phase': fleet.get('phase'),
                                                                'tables': table_progress(fleet, runs), 'frames': frames, 'frame_times': frame_times})
                stop['flag'] = stop['flag'] or bool(reply.get('stop'))
            results = run_trials(args, editor, run_variants, clients, run, progress, lambda: stop['flag'],
                                 title='Fleet (web): %d replica tables' % len(run_variants), after_cleanup=restore_preview_after_cleanup)
            print_table(results)
            for r in results:
                path = r.get('videos', {}).get('overhead')
                if path and Path(path).stat().st_size < 1_400_000:
                    try:
                        server.api('/api/fleet/worker/video', {'request_id': request_id, 'table': r['table'],
                                                               'video': base64.b64encode(Path(path).read_bytes()).decode()}, timeout=30)
                    except Exception as error:
                        print('video upload failed for %s: %s' % (r['table'], error), flush=True)
            held = [r['table'] for r in results if r['lift'].get('lift_held')]
            status = 'stopped' if stop['flag'] else 'done'
            server.event('fleet.finished', request_id, ('Measured held lift on ' + ', '.join(held) + ' (review videos)') if held
                         else 'No table measured a held 10 cm lift')
            summary = [{k: r.get(k) for k in ('batch_end_lift', 'policy_session_released', 'policy_session_release_error', 'cup_metrics', 'batch_end_cup_metrics', 'measured_cup', 'table', 'name', 'prompt', 'calls', 'requests', 'motion_chunks', 'outcome', 'objective', 'verification', 'cycles_run', 'cycles_planned', 'lift', 'error', 'stopped_reason', 'threshold_hold', 'assist')} for r in results]
        except Exception as error:
            print('fleet run failed: %s' % error, flush=True)
            server.event('fleet.failed', request_id, str(error))
            summary = []
        finally:
            publication_stop.set()
            if publication_thread is not None: publication_thread.join(timeout=11)
        try:
            server.api('/api/fleet/worker/done', {'request_id': request_id, 'status': status, 'results': summary})
        except Exception as error:
            print('done report failed: %s' % error, flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--project', type=Path, default=DEFAULT_PROJECT)
    p.add_argument('--config', type=Path, default=HERE/'fleet_cups.json')
    p.add_argument('--data', type=Path, default=None, help='default <project>/Saved/DreamFleet')
    p.add_argument('--dry-run', action='store_true', help='hold poses; no Dream API calls')
    p.add_argument('--scene-only', action='store_true', help='extend the hall with the replica booths and exit')
    p.add_argument('--clear', action='store_true', help='remove the shared replica booths and exit')
    p.add_argument('--local', action='store_true', help='editor-only scratch copies (scene only)')
    p.add_argument('--clear-local', action='store_true', help='remove local scratch copies and exit')
    p.add_argument('--fps', type=float, default=2.)
    p.add_argument('--max-seconds', type=int, default=170)
    p.add_argument('--cycles', type=int, default=None, help="override every table's cycle count")
    p.add_argument('--tables', default=None, help='comma-separated table numbers to run, e.g. 1,3,5')
    p.add_argument('--serve', action='store_true', help='run commands queued from the Luna2 web page')
    p.add_argument('--reload-config', action='store_true', help='re-read --config before each web command (checkpoints must not change)')
    p.add_argument('--maintenance',action='store_true',help='serve live cameras but never claim commands or resets; restart without flag to enable work')
    p.add_argument('--server', default='http://100.84.28.59:8786')
    p.add_argument('--token-file', type=Path, default=None, help='default <project>/Saved/DreamDebugger/worker-token')
    args = p.parse_args()

    config = json.loads(args.config.read_text())
    if args.cycles:
        for table in config['tables']:
            table['cycles'] = args.cycles
    if args.serve:
        args.token_file = args.token_file or args.project/'Saved/DreamDebugger/worker-token'
        return serve(args, config)
    if args.tables:
        keep = [int(x) for x in args.tables.split(',')]
        config['tables'] = [dict(t, index=t.get('index', i)) for i, t in enumerate(config['tables']) if t.get('index', i) in keep]
    from pregrasp import prepare_run_config
    config = prepare_run_config(config, config)
    variants = normalize_config(config)
    data = args.data or args.project/'Saved/DreamFleet'
    run = new_run(data, config)
    editor = Editor(args.project)

    if args.clear or args.clear_local or args.scene_only:
        title = ('Fleet: remove %s replica tables' % ('local scratch' if args.clear_local else 'shared')
                 if args.clear or args.clear_local else 'Fleet: extend hall with %d replica tables' % len(variants))
        wait_for_editor(lambda: editor.call('begin_task', {'title': title, 'request': 'Replica Maker Arm tables in booths extending the hall along +Y',
                                                           'keep_view': True, 'focus_x': 40, 'focus_y': 0, 'focus_z': 100}),
                        lambda: False, lambda m: print(m, flush=True), timeout=900)
        note = ''
        try:
            if args.clear_local:
                print(editor.deferred('clear_local', [], run/'clear-result.json'), flush=True)
                note = 'Removed local scratch replicas.'
            elif args.clear:
                print(editor.scene('maker_arm_fleet.clear()', 'Fleet: remove shared replica tables'), flush=True)
                note = 'Removed shared replica tables.'
            elif args.local:
                scene = editor.deferred('setup_local', [str(run/'config.json')], run/'scene-result.json', timeout=180)
                note = 'Built %d local scratch actors.' % scene['replica_actors']
            else:
                for v in variants:
                    out = editor.scene('maker_arm_fleet.build_table(%r, %d)' % (str(run/'config.json'), v['index']),
                                       'Fleet: booth T%02d (%s)' % (v['index'], v['name']))
                    print('T%02d %s' % (v['index'], out.splitlines()[-1][:160]), flush=True)
                print(editor.scene('maker_arm_fleet.label_main()', 'Fleet: label the main table T00').splitlines()[-1], flush=True)
                out = editor.scene('maker_arm_fleet.reset(%r)' % str(run/'config.json'), 'Fleet: set start poses')
                print('Start poses: ' + out.splitlines()[-1][:300], flush=True)
                note = 'Extended the hall with %d labelled replica booths.' % len(variants)
            print(note, flush=True)
        finally:
            editor.call('end_task', {'summary': note or 'Fleet scene task ended early.', 'keep_view': True})
        return

    clients = {}
    if not args.dry_run:
        clients = warm_clients(variants, run)
        variants = [v for v in variants if v['index'] in clients]
        if not variants:
            raise SystemExit('No Dream session could be configured')
    try:
        results = run_trials(args, editor, variants, clients, run)
    finally:
        for client in clients.values():
            try:
                client.close()
            except Exception:
                pass
    print_table(results)
    print('\nSummary:', run/'summary.json', flush=True)


if __name__ == '__main__':
    main()
