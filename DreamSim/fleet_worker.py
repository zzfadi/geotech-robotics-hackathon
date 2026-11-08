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
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO/'Unreal/TheHumanTouch/Tools'))
from maker_arm_fleet_layout import normalize_config, JAW_MAX_M  # noqa: E402
from queueing import wait_for_editor  # noqa: E402

FLASH = 'dreamlabs/dream-1-beta-flash'
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


def lift_verdict(table_status, target, hold_seconds=2.0):
    """Numbers only. Never a grasp claim: video review is still required."""
    p = (table_status or {}).get('props', {}).get(target)
    if not p:
        return {'measured': False}
    return {'measured': True,
            'height_cm': p.get('height_above_start_cm', 0.),
            'max_height_cm': p.get('max_z_cm', 0.) - p.get('initial_z_cm', 0.),
            'held_slow_above_10cm_s': p.get('current_slow_above_10cm_seconds', 0.),
            'max_held_slow_above_10cm_s': p.get('max_slow_above_10cm_seconds', 0.),
            'physics_simulated': p.get('physics_simulated'), 'attached_to': p.get('attached_to'),
            'gravity_enabled': p.get('gravity_enabled'),
            'lift_held': (p.get('current_slow_above_10cm_seconds', 0.) >= hold_seconds
                          and p.get('height_above_start_cm', 0.) >= 10
                          and p.get('gravity_enabled') is True
                          and p.get('physics_simulated') is True and not p.get('attached_to'))}


class Editor:
    def __init__(self, project):
        self.project = Path(project)
        self.lock = threading.Lock()

    def call(self, tool, args, timeout=60):
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
        self.result = {'table': self.key, 'name': variant['name'], 'prompt': variant['prompt'],
                       'checkpoint': variant['checkpoint'], 'position_ik': variant['position_ik'],
                       'pad_contacts': variant['pad_contacts'], 'action_rows': variant['action_rows'],
                       'cycles_planned': variant['cycles'], 'cycles_run': 0, 'calls': 0,
                       'lift': {'measured': False}, 'error': None, 'stopped_reason': None, 'events': [],
                       'requests': [], 'motion_chunks': []}

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
        return fleet.get('tables', {}).get(self.key)

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
            obs = {'prompt': self.v['prompt']}
            for side in ('left', 'right'):
                obs['state.'+side+'_arm'] = p['state']['q_'+side]
                obs['state.'+side+'_grip'] = [p['state']['gap_'+side+'_m']/JAW_MAX_M]
            self.client.set_observation(p['frames'], obs, timestamp=p['captured_monotonic'])
            return p

        def call():
            self.result['calls'] += 1
            request = {'call': self.result['calls'], 'started_unix': time.time(),
                       'observation_unix': holder['packet']['captured_unix']}
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
        for attempt in range(4):
            while not _INFERENCE_SLOTS.acquire(timeout=.2):
                if self.stopped():
                    raise RuntimeError('Stopped waiting for Dream capacity')
            try:
                return request_action(call, refresh, self.stopped, lambda m: self.log(m), DreamSessionExpired)
            except Exception as error:
                if 'NO_CAPACITY' not in str(error) or attempt == 3:
                    raise
                self.log('Dream capacity busy; bounded retry %d/3' % (attempt+1))
            finally:
                _INFERENCE_SLOTS.release()
            deadline = time.monotonic() + 2**(attempt+1) + self.v['index']*.1
            while time.monotonic() < deadline:
                if self.stopped():
                    raise RuntimeError('Stopped during Dream capacity backoff')
                time.sleep(.1)

    def execute(self):
        after = time.time() - 1
        target = self.v['target']['label']
        for cycle in range(self.v['cycles']):
            if self.stopped():
                self.result['stopped_reason'] = 'fleet stopped'
                return
            packet = self.fresh(after)
            started = time.monotonic()
            action, used = self.infer(packet)
            packet = used or packet
            raw, ik = action, None
            write_json(self.dir/('raw-action-%02d.json' % (cycle+1)),
                       {'state': packet['state'], 'action': raw, 'captured_unix': packet['captured_unix']})
            if self.v['position_ik'] and not self.args.dry_run:
                from position_ik import adapt
                action, ik = adapt(raw, packet['state'], self.v['action_rows'] or 12)
            report = self.dir/('action-%02d.json' % (cycle+1))
            write_json(report, {'state': packet['state'], 'action': action, 'raw_action': raw if ik else None,
                                'ik_report': ik, 'execution_rows': None if self.args.dry_run else self.v['action_rows'],
                                'checkpoint': None if self.args.dry_run else self.v['checkpoint'],
                                'controller_source': 'dry-run hold' if self.args.dry_run else
                                ('Dream EEF + position IK' if ik else 'Dream joint targets')})
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
            motion_evidence = dict(playback)
            motion_evidence['max_joint_delta_rad'] = max(abs(a-b)
                for side in ('left', 'right')
                for a, b in zip(packet['state']['q_'+side], playback.get('q_'+side, packet['state']['q_'+side])))
            self.result['motion_chunks'].append(motion_evidence)
            after = (playback.get('finished_unix') or time.time()) - .001
            self.result['cycles_run'] = cycle+1
            lift = lift_verdict(self.status(), target)
            self.result['lift'] = lift
            self.log('cycle %d done (inference %.2fs) lift %.2f cm' % (cycle+1, inference, lift.get('height_cm', 0.)))
            if lift.get('height_cm', 0) >= 10:
                self.log('target above 10 cm; holding pose for 2.2 s to measure retention')
                time.sleep(2.2)
                lift = lift_verdict(self.status(), target)
                self.result['lift'] = lift
                if lift.get('lift_held'):
                    self.result['stopped_reason'] = 'measured held lift (needs video review)'
                    self.log('measured held lift %.2f cm for %.2f s' % (lift['height_cm'], lift['held_slow_above_10cm_s']))
                    return
        self.result['stopped_reason'] = 'cycles exhausted'

    def __call__(self):
        try:
            self.execute()
        except Exception as error:
            self.result['error'] = str(error)
            self.log('error: ' + str(error))
        if self.args.dry_run:
            self.result['outcome'] = 'dry_run'
        elif self.stopped():
            self.result['outcome'] = 'stopped'
        elif self.result['error']:
            self.result['outcome'] = 'error'
        elif not self.v.get('objective'):
            self.result['outcome'] = 'unverified'
        else:
            self.result['outcome'] = 'passed' if self.result['lift'].get('lift_held') else 'failed'
        self.result['objective'] = self.v.get('objective')
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
            checkpoint = FLASH if variant['checkpoint'] == 'auto' else variant['checkpoint']
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
        done = subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-framerate', str(rate), '-i', str(folder/pattern),
                               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(target)], capture_output=True, timeout=60)
        if done.returncode == 0:
            out[role] = str(target)
    return out


def latest_overhead(run, key):
    """Newest complete overhead JPEG for a live thumbnail (review frame or policy packet)."""
    frames = sorted((run/key/'recording').glob('overhead_rec_*.jpg'))[-2:]
    for path in reversed(frames):
        data = path.read_bytes()
        if data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
            return data, path.stat().st_mtime
    packet = read_json(run/key/'observation.json')
    if packet and packet.get('frames', {}).get('video.overhead'):
        import base64
        return base64.b64decode(packet['frames']['video.overhead']), packet['captured_unix']
    return None, None


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


def run_trials(args, editor, variants, clients, run, progress=None, should_stop=None, title=None):
    """Hold the editor task, reset start poses, run every table in one SIE session.

    progress(fleet_json, runs) is called about once a second; should_stop() is
    polled and stops the whole fleet. Returns the per-table results (best first)."""
    config_path = run/'config.json'
    title = title or 'Fleet: %d parallel %s trials' % (len(variants), 'dry-run' if args.dry_run else 'Dream')
    wait_for_editor(lambda: editor.call('begin_task', {'title': title, 'request': 'Replica Maker Arm tables in the open-top grid; parallel simulation',
                                                       'keep_view': True, 'focus_x': 40, 'focus_y': 0, 'focus_z': 100}),
                    lambda: bool(should_stop and should_stop()), lambda m: print(m, flush=True), timeout=900)
    results, note, runs = [], '', []
    done = threading.Event()

    def monitor():
        while not done.wait(1.):
            try:
                if should_stop and should_stop():
                    (run/'stop').touch()
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
    for result in results:
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


def table_progress(fleet, runs):
    out = {}
    tables = fleet.get('tables', {})
    for r in runs:
        status = tables.get(r.key, {})
        lift = lift_verdict(status, r.v['target']['label'])
        out[r.key] = {'calls': r.result['calls'], 'returned_actions': sum(bool(q.get('returned_action')) for q in r.result['requests']),
                      'cycles_run': r.result['cycles_run'], 'cycles_planned': r.result['cycles_planned'],
                      'lift': lift, 'error': r.result['error'] or status.get('error'),
                      'stopped_reason': r.result['stopped_reason'], 'moving': status.get('moving')}
    return out


class IdlePreview:
    def __init__(self, editor, directory, server):
        self.editor, self.directory, self.server = editor, Path(directory), server
        self.directory.mkdir(parents=True, exist_ok=True)
        self.running = False
        self.sent = {}
        self.retry_after = 0.

    def set_enabled(self, enabled):
        if bool(enabled) == self.running or (enabled and time.monotonic() < self.retry_after):
            return
        # Local capture setup only; no shared geometry changes or long-held task.
        self.editor.call('begin_task', {'title': 'Fleet idle cameras', 'request': 'Update opt-in local table camera preview', 'keep_view': True, 'focus_x': 40, 'focus_y': 0, 'focus_z': 100})
        try:
            function = 'start_preview' if enabled else 'stop_preview'
            self.editor.deferred(function, [str(self.directory)] if enabled else [], self.directory/'control-result.json')
            self.running = bool(enabled)
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
        frames, stamps = {}, {}
        for key, path in packet.get('frames', {}).items():
            stamp = packet.get('frame_times', {}).get(key)
            if not stamp or stamp == self.sent.get(key):
                continue
            data = Path(path).read_bytes()
            if data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
                frames[key], stamps[key] = base64.b64encode(data).decode(), stamp
        if frames:
            self.server.api('/api/fleet/worker/preview', {'frames': frames, 'frame_times': stamps})
            self.sent.update(stamps)


def serve(args, config):
    """Daemon: warm Dream sessions, then run whatever the web page queues."""
    import base64
    from fleet_planning import apply_plan
    server = Server(args.server, args.token_file)
    variants = normalize_config(config)
    data = args.data or args.project/'Saved/DreamFleet'
    data.mkdir(parents=True, exist_ok=True)
    acquire_worker_lock(data)
    setup_errors = {}
    clients = {} if args.dry_run else warm_clients(variants, data, setup_errors)
    usable = [v for v in variants if args.dry_run or v['index'] in clients]
    hello = [{**v, 'checkpoint': v['checkpoint'] if not args.dry_run else 'dry-run (no Dream calls)',
              'ready': args.dry_run or v['index'] in clients, 'error': setup_errors.get(v['index'])} for v in variants]
    editor = Editor(args.project)
    preview = IdlePreview(editor, data/'preview', server)
    seen = set()
    print('Fleet worker ready: %d tables%s' % (len(usable), ' (dry run)' if args.dry_run else ''), flush=True)
    while True:
        try:
            server.api('/api/fleet/worker/hello', {'tables': hello})
            next_reply = server.api('/api/fleet/worker/next')
            command = next_reply['command']
        except Exception as error:
            print('debugger unreachable: %s' % error, flush=True)
            time.sleep(3)
            continue
        if not command or command['status'] != 'running' or command['id'] in seen:
            try:
                preview.set_enabled(next_reply.get('preview_enabled', False))
                if preview.running:
                    preview.publish()
            except Exception as error:
                print('idle preview unavailable: %s' % error, flush=True)
            time.sleep(1)
            continue
        seen.add(command['id'])
        request_id = command['id']
        chosen = [dict(t) for t in config['tables']]
        run_config = {'defaults': config.get('defaults', {}), 'tables': []}
        for i, table in enumerate(chosen, 1):
            if i in command['tables'] and (args.dry_run or i in clients):
                table['index'] = i
                if command.get('prompt'):
                    table['prompt'] = command['prompt']
                run_config['tables'].append(table)
        if command.get('plan'):
            run_config = apply_plan(config, command['plan'])
        status = 'failed'
        stop = {'flag': False}
        try:
            preview.set_enabled(False)
            missing = [i for i in command['tables'] if not args.dry_run and i not in clients]
            if missing:
                raise RuntimeError('Selected Dream sessions are unavailable: ' + ', '.join(map(str, missing)))
            if not run_config['tables']:
                raise RuntimeError('No selected table has a ready Dream session')
            run = new_run(data, run_config)
            run_variants = normalize_config(run_config)
            for v in run_variants:
                if v['index'] in clients:
                    clients[v['index']].reset_episode()
            server.event('fleet.started', request_id, 'Running ' + ' '.join('T%02d' % v['index'] for v in run_variants) + ' in parallel')

            def progress(fleet, runs):
                frames, frame_times = {}, {}
                for v in run_variants:
                    data, captured_at = latest_overhead(run, 'T%02d' % v['index'])
                    if data:
                        frames['T%02d' % v['index']] = base64.b64encode(data).decode()
                        frame_times['T%02d' % v['index']] = captured_at
                reply = server.api('/api/fleet/worker/status', {'request_id': request_id, 'phase': fleet.get('phase'),
                                                                'tables': table_progress(fleet, runs), 'frames': frames, 'frame_times': frame_times})
                stop['flag'] = stop['flag'] or bool(reply.get('stop'))
            results = run_trials(args, editor, run_variants, clients, run, progress, lambda: stop['flag'],
                                 title='Fleet (web): %d replica tables' % len(run_variants))
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
            summary = [{k: r[k] for k in ('table', 'name', 'prompt', 'calls', 'requests', 'motion_chunks', 'outcome', 'objective', 'cycles_run', 'cycles_planned', 'lift', 'error', 'stopped_reason')} for r in results]
        except Exception as error:
            print('fleet run failed: %s' % error, flush=True)
            server.event('fleet.failed', request_id, str(error))
            summary = []
        try:
            server.api('/api/fleet/worker/done', {'request_id': request_id, 'status': status, 'results': summary})
        except Exception as error:
            print('done report failed: %s' % error, flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--project', type=Path, default=DEFAULT_PROJECT)
    p.add_argument('--config', type=Path, default=HERE/'fleet_variants.json')
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
        config['tables'] = [dict(t, index=i) for i, t in enumerate(config['tables'], 1) if i in keep]
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
