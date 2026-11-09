#!/usr/bin/env python3
"""Mac-side Table 10 agent: live camera upload, test-button relay and link health.

- Keeps Tools/maker_arm_real_link.py (Table 10 <-> real arms) and the live Table 10 camera
  (Tools/maker_arm_table10_camera.py) running in the local editor, restarting them if they stall.
- Uploads every new Table 10 frame (up to 10 fps) to the web page.
- Relays the web page's test buttons (move_up / idle / stop) to the link via
  Saved/RealArmLink/command.json and reports link, bridge and sync status back.
- Starts and supervises the headless real-arm services (Tools/real_arm_service.py, one per arm,
  maker-arm-sdk venv) at all times, with or without the editor, and turns the web page's drive
  request into the services' heartbeat (control.json). No heartbeat -> the service returns the arms
  and releases torque.
- Web control works without Unreal: while an arm is torque-on, the test buttons go to the services
  (control.json "test"), which run them on the real arms; the editor link, if running, mirrors them.
  With both arms off, the buttons play a virtual preview in the editor.
- Sends an Unreal-independent arm view (forward kinematics of the real and the Table 10 joints).

    python3 DreamSim/table10_agent.py [--server http://100.84.28.59:8786]
"""
import argparse
import base64
import json
import math
import subprocess
import threading
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = Path('/Users/geo_dennis/Desktop/Projects/geotech-robotics-hackathon/geotech-robotics-hackathon/Unreal/TheHumanTouch')
TOOLS = PROJECT/'Tools'
LINK_DIR = PROJECT/'Saved/RealArmLink'
CAMERA_DIR = PROJECT/'Saved/Table10Stream'
HEARTBEAT_S = .25
RESTART_EVERY_S = 30.
SDK_PYTHON = Path('/Users/geo_dennis/Desktop/Projects/geotech-robotics-hackathon/maker-arm-sdk/.venv/bin/python')
ADAPTERS = {'left': '003A003F4146570320303038', 'right': '0027002E4146570520313238'}
SERVER_LOST_S = 15.  # drive heartbeat stops when the web server has not answered for this long (a deploy restart
                     # takes a few seconds; the server keeps the drive request across it)

EDITOR_CODE = '''import sys, importlib, json
T = r"%s"
T in sys.path or sys.path.insert(0, T)
out = {}
%s
print("AGENT_RESULT " + json.dumps(out, default=str))
''' % (TOOLS, '%s')
START_LINK = '''import maker_arm_real_tests, maker_arm_real_link as link
importlib.reload(maker_arm_real_tests)
link = importlib.reload(link)
out["link"] = link.start()'''
# The capture actor must be created outside the run_python transaction: start on the next tick.
START_CAMERA = '''import unreal, maker_arm_table10_camera as cam
cam = importlib.reload(cam)
_once = {}
def _start_camera(delta):
    unreal.unregister_slate_post_tick_callback(_once["handle"])
    try:
        cam.start(r"%s")
    except Exception as error:
        unreal.log_error("Table 10 camera start: %%s" %% error)
_once["handle"] = unreal.register_slate_post_tick_callback(_start_camera)
out["camera"] = "scheduled"''' % CAMERA_DIR


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError, OSError):
        return None


def write_json(path, value):
    tmp = Path(path).with_suffix('.tmp')
    tmp.write_text(json.dumps(value))
    tmp.replace(path)


def editor(snippet, timeout=60):
    """Run a snippet in the local editor through the CoPresence MCP (local actors only)."""
    result = subprocess.run(['uv', 'run', '-q', '--script', 'Tools/copresence_mcp.py', 'exec-python', '8041',
                             EDITOR_CODE % snippet], cwd=PROJECT, capture_output=True, text=True, timeout=timeout)
    for line in result.stdout.splitlines():
        if line.startswith('AGENT_RESULT '):
            return json.loads(line[len('AGENT_RESULT '):])
    raise RuntimeError((result.stdout+result.stderr)[-600:])


class EditorCalls:
    """Editor snippets on worker threads. A busy or hung editor (a crash stalls exec-python for its full 60 s
    timeout) must never stall the heartbeat: it is the drive dead-man and what keeps the web controls online."""

    def __init__(self):
        self.busy = set()

    def start(self, name, snippet, report):
        if name in self.busy:
            return False
        self.busy.add(name)

        def run():
            try:
                print('starting Table 10 %s: %s' % (name, report(editor(snippet))), flush=True)
            except Exception as error:
                print('%s start failed: %s' % (name, str(error)[-200:]), flush=True)
            finally:
                self.busy.discard(name)
        threading.Thread(target=run, daemon=True, name='editor-' + name).start()
        return True


def editor_running():
    return subprocess.run(['pgrep', '-f', 'UnrealEditor.app/Contents/MacOS/UnrealEditor'], capture_output=True).returncode == 0


def interactive_bridge_running():
    return subprocess.run(['pgrep', '-f', 'real_arm_bridge.py'], capture_output=True).returncode == 0


def stop_legacy_dual_service():
    """Stop a service started the old way with both adapters in one process."""
    out = subprocess.run(['pgrep', '-fl', 'real_arm_service.py'], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if '--left' in line and '--right' in line:
            subprocess.run(['kill', '-TERM', line.split()[0]])
            print('stopped legacy two-arm service %s' % line.split()[0], flush=True)


MODE_ORDER = ('drive', 'returning', 'hold', 'mirror', 'off')
FINAL = ('done', 'rejected', 'aborted', 'stopped', 'error')
sys.path.insert(0, str(TOOLS))
try:
    import maker_arm_real_mapping as mapping  # pure Python URDF kinematics (no Unreal)
except Exception as error:  # the arm view is optional
    mapping = None
    print('arm view disabled: %s' % error, flush=True)
VIEW_BASES = {'left': (20., -40., 76.), 'right': (20., 40., 76.)}  # station-local cm (maker_arm_station.BASES)


def arm_points(arms):
    """Joint-frame positions per side for the web arm view, as 'x,y,z x,y,z ...' (cm, station-local).
    A string, because the server clips telemetry nested deeper than four levels."""
    out = {}
    if mapping is None:
        return out
    for side, arm in (arms or {}).items():
        q = arm.get('q')
        if side not in VIEW_BASES or not q or len(q) != 6:
            continue
        try:
            fk = mapping.fk_ue(q, arm.get('gap_m', .04), math.pi, VIEW_BASES[side])
            out[side] = ' '.join(','.join('%.1f' % v for v in fk[name][0]) for name in mapping.CHAIN)
        except Exception:
            pass
    return out


def merged_test(services, test_id):
    """The per-arm services' results for one web test, as one test record for the server."""
    parts = [st.get('test') for st in (s.status() for s in services)]
    parts = [t for t in parts if t and t.get('id') == test_id]
    if not parts:
        return None
    states = [t.get('state') for t in parts]
    state = ('running' if 'running' in states else next((x for x in ('error', 'aborted', 'rejected', 'stopped') if x in states), 'done'))
    return {'id': test_id, 'action': parts[0].get('action'), 'state': state,
            'detail': '; '.join('%s: %s' % (t.get('side'), t.get('detail')) for t in parts)[:300],
            'progress': min(t.get('progress') or 0 for t in parts), 'lag_deg': max(t.get('lag_deg') or 0 for t in parts),
            'source': 'real-arm service'}


def merged_status(services):
    """One status for the web from the per-arm services."""
    now = time.time()
    parts = {s.side: s.status() for s in services}
    live = {side: st for side, st in parts.items() if now - st.get('unix', 0) < 3 and st.get('running')}
    if not live:
        return {'running': False, 'mode': 'off', 'message': 'real-arm services not running'}
    modes = [st.get('mode') for st in live.values()]
    mode = next((m for m in MODE_ORDER if m in modes), 'off')
    message = '; '.join('%s: %s' % (side, st.get('message')) for side, st in sorted(live.items()) if st.get('message'))
    out = {'running': True, 'mode': mode, 'message': message[:400], 'modes': {side: st.get('mode') for side, st in live.items()},
           'sources': {side: st.get('source') for side, st in live.items() if st.get('source')},
           'messages': {side: (st.get('message') or '')[:200] for side, st in live.items()},
           'acks': {side: st.get('ack') for side, st in live.items() if st.get('ack')},
           'answering': {}, 'bus': {}, 'holding': [], 'driving': []}
    for st in live.values():
        out['answering'].update(st.get('answering') or {})
        out['bus'].update(st.get('bus') or {})
        out['holding'] += st.get('holding') or []
        out['driving'] += st.get('driving') or []
    return out


def merged_real():
    """Per-arm real_state.json files merged (falls back to the single legacy file)."""
    out = {'arms': {}, 'mode': None, 'unix': 0}
    for side in ('left', 'right'):
        d = read_json(LINK_DIR/side/'real_state.json') or {}
        if d and time.time()-d.get('unix', 0) < 1:
            out['arms'].update(d.get('arms') or {})
            out['unix'] = max(out['unix'], d.get('unix', 0))
            out['mode'] = 'drive' if d.get('mode') == 'drive' or out['mode'] == 'drive' else (out['mode'] or d.get('mode'))
    if out['arms']:
        return out
    legacy = read_json(LINK_DIR/'real_state.json') or {}  # the old two-arm service; only if it is live
    return legacy if time.time()-legacy.get('unix', 0) < 1 else {}


class RealArmService:
    """One headless real-arm service process per arm. Two USB-CAN adapters in one process lost motor
    feedback right after enable; one adapter per process (like the SDK's own tools) drives cleanly."""
    def __init__(self, side):
        self.side = side
        self.dir = LINK_DIR / side
        self.dir.mkdir(parents=True, exist_ok=True)
        self.pattern = 'real_arm_service.py --%s ' % side
        self.process = None
        self.started = 0.
        self.editor_gone = None

    def running(self):
        # Also adopts this arm's service left by a previous agent run (it holds the adapter).
        if self.process is not None and self.process.poll() is None:
            return True
        return subprocess.run(['pgrep', '-f', self.pattern], capture_output=True).returncode == 0

    def status(self):
        return read_json(self.dir/'service.json') or {}

    def log_tail(self, n=8):
        try:
            lines = (self.dir/'service.log').read_text(errors='replace').splitlines()
        except OSError:
            return []
        return ['%s: %s' % (self.side, l) for l in lines if l and not l.startswith(('[mirror] left', '[mirror] right', '[drive] left', '[drive] right', 'gs_usb adapter'))][-n:]

    def supervise(self, now):
        # Always on, editor or not: the web page controls the arms without Unreal.
        if True:
            if (not self.running() and now-self.started > RESTART_EVERY_S and SDK_PYTHON.exists()
                    and not interactive_bridge_running()):
                self.started = now
                log = open(self.dir/'service.log', 'a')
                self.process = subprocess.Popen(
                    [str(SDK_PYTHON), '-u', 'real_arm_service.py', '--%s' % self.side, ADAPTERS[self.side],
                     '--state-dir', str(self.dir), '--ue-state', str(LINK_DIR/'ue_state.json'), '--control', str(LINK_DIR/'control.json')],
                    cwd=TOOLS, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
                print('started %s real-arm service (pid %d)' % (self.side, self.process.pid), flush=True)

    def stop(self, why):
        if self.running():
            print('stopping %s real-arm service: %s' % (self.side, why), flush=True)
            subprocess.run(['pkill', '-TERM', '-f', self.pattern])  # verified return + torque off, exit
            for _ in range(40):
                if not self.running():
                    return
                time.sleep(.5)
            print('real-arm service did not exit within 20 s (holding?)', flush=True)


class Server:
    def __init__(self, url, token_file):
        self.url = url.rstrip('/')
        self.token = Path(token_file).read_text().strip()

    def post(self, path, data, timeout=5):
        request = urllib.request.Request(self.url+path, data=json.dumps(data).encode(), headers={
            'Authorization': 'Bearer '+self.token, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)


def sync_deg(real, ue):
    out = {}
    for side in ('left', 'right'):
        a = ((real or {}).get('arms') or {}).get(side, {}).get('q')
        b = ((ue or {}).get('arms') or {}).get(side, {}).get('q')
        if a and b:
            out[side] = round(max(abs(math.degrees(x-y)) for x, y in zip(a, b)), 2)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--server', default='http://100.84.28.59:8786')
    p.add_argument('--token-file', type=Path, default=PROJECT/'Saved/DreamDebugger/worker-token')
    args = p.parse_args()
    server = Server(args.server, args.token_file)
    LINK_DIR.mkdir(parents=True, exist_ok=True)
    CAMERA_DIR.mkdir(parents=True, exist_ok=True)
    restarted = {'link': 0., 'camera': 0.}
    services = [RealArmService('left'), RealArmService('right')]
    calls = EditorCalls()
    stop_legacy_dual_service()
    drive = {'on': False, 'seq': 0}
    release_seq = 0
    server_ok = 0.
    last_frame_seq = None
    camera_advance = time.monotonic()
    claimed = None
    web_test = None      # the latest test handed to the real-arm services (control.json "test")
    local_test = None    # a test the agent itself had to refuse
    routed = {}          # test id -> 'service' | 'editor'
    routed_at = {}
    control_sent = {}
    next_beat = 0.
    print('Table 10 agent: server %s' % args.server, flush=True)
    while True:
        now = time.monotonic()
        # ---- live camera: upload every new frame -----------------------------------------
        info = read_json(CAMERA_DIR/'T10.json') or {}
        seq = info.get('sequence')
        if seq is not None and seq != last_frame_seq and info.get('running'):
            try:
                data = (CAMERA_DIR/'T10.jpg').read_bytes()
                if data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
                    server.post('/api/interaction/worker/preview',
                                {'frame': base64.b64encode(data).decode(), 'captured_unix': info['captured_unix']})
                last_frame_seq = seq
                camera_advance = now
            except urllib.error.HTTPError as error:
                last_frame_seq = seq  # e.g. 409 old frame; skip it
                if error.code != 409:
                    print('frame upload: HTTP %s' % error.code, flush=True)
            except (OSError, ValueError) as error:
                print('frame upload: %s' % error, flush=True)
        # ---- heartbeat, buttons, health ---------------------------------------------------
        if now >= next_beat:
            next_beat = now+HEARTBEAT_S
            real, ue = merged_real(), read_json(LINK_DIR/'ue_state.json')
            real_age = time.time()-(real or {}).get('unix', 0)
            ue_age = time.time()-(ue or {}).get('unix', 0)
            status = merged_status(services)
            test = (ue or {}).get('test')
            if claimed and routed.get(claimed) == 'service':
                test = merged_test(services, claimed) or (
                    {'id': claimed, 'state': 'sent', 'detail': 'sent to the real-arm services'} if time.time()-routed_at.get(claimed, 0) < 5
                    else {'id': claimed, 'state': 'error', 'detail': 'no arm service took the test (are the arms on?)'})
            elif local_test and local_test.get('id') == claimed:
                test = local_test
            elif claimed and routed.get(claimed) == 'editor' and test and test.get('id') == claimed:
                test = {**test, 'source': 'unreal preview'}
            report = {
                'link': {'mode': (ue or {}).get('mode'), 'age_s': round(ue_age, 2), 'test': test,
                         'world': (ue or {}).get('world'),
                         'q_deg': {s: [round(math.degrees(v), 1) for v in a.get('q', [])] for s, a in ((ue or {}).get('arms') or {}).items()}},
                'bridge': {'mode': (real or {}).get('mode'), 'age_s': round(real_age, 2),
                           'q_deg': {s: [round(math.degrees(v), 1) for v in a.get('q', [])] for s, a in ((real or {}).get('arms') or {}).items()}},
                'sync_deg': sync_deg(real, ue) if real_age < 1 and ue_age < 1 else {},
                'camera': {'running': bool(info.get('running')), 'sequence': seq, 'error': info.get('error'),
                           'age_s': round(time.time()-info.get('captured_unix', 0), 2) if info else None},
                'claimed': claimed,
                'view': {'real': arm_points((real or {}).get('arms')),
                         'virtual': arm_points((ue or {}).get('arms')) if ue_age < 1 else {}}}
            report['service'] = {**status, 'interactive_bridge': interactive_bridge_running() if not status.get('running') else False,
                                 'control': control_sent}
            report['log'] = sum((s.log_tail() for s in services), [])[-12:]
            try:
                reply = server.post('/api/table10/agent', report)
                server_ok = now
                drive = reply.get('drive') or drive
                release_seq = reply.get('release_seq', release_seq)
                command = reply.get('command')
                if command and command.get('id') != claimed:
                    claimed = command['id']
                    routed_at[claimed] = time.time()
                    item = {'id': command['id'], 'action': command['action'], 'unix': time.time()}
                    torque_on = any(m == 'drive' for m in (status.get('modes') or {}).values())
                    if torque_on:  # the real-arm services run it (no Unreal needed); the editor mirrors
                        web_test, routed[claimed] = item, 'service'
                        where = 'real-arm services'
                    elif ue_age < 3:  # arms off: virtual preview in the editor
                        write_json(LINK_DIR/'command.json', item)
                        routed[claimed], where = 'editor', 'editor (virtual preview)'
                    else:
                        local_test = {**item, 'state': 'rejected',
                                      'detail': 'Unreal is not running and the arms are off: turn the arms ON to test the real arms'}
                        routed[claimed], where = 'agent', 'nobody (refused)'
                    print('test command: %s (%s) -> %s' % (command['action'], command['id'][:8], where), flush=True)
            except (OSError, ValueError) as error:
                print('heartbeat: %s' % error, flush=True)
            # Drive heartbeat for the real-arm service: only while the server answers.
            control = {'unix': time.time(), 'source': 'web', 'drive': bool(drive.get('on')) and now-server_ok < SERVER_LOST_S,
                       'drive_seq': drive.get('seq'), 'release_seq': release_seq,
                       'sides': drive.get('sides') or ['left', 'right'], 'test': web_test}
            write_json(LINK_DIR/'control.json', control)
            control_sent = {'drive': control['drive'], 'drive_seq': control['drive_seq'], 'release_seq': release_seq,
                            'test': (web_test or {}).get('id')}  # reported next beat: what the services were told
            # A drive request the service could not engage within its window: clear it so the
            # page shows the refusal and Enable drive can be pressed again.
            if drive.get('on') and status.get('mode') in ('mirror', 'off') and drive.get('since') and time.time()-drive['since'] > 13:
                try:
                    server.post('/api/table10/test', {'action': 'drive_off', 'source': 'engage failed'})
                    print('drive request cleared: not engaged (%s)' % status.get('message'), flush=True)
                    drive = {**drive, 'on': False}
                except (OSError, ValueError) as error:
                    print('drive clear: %s' % error, flush=True)
            for service in services:
                service.supervise(now)
            # Restart the link or camera when they stop advancing (e.g. after an editor restart).
            stale_link = ue_age > 3 and now-restarted['link'] > RESTART_EVERY_S
            stale_camera = (now-camera_advance > 5 and now-restarted['camera'] > RESTART_EVERY_S
                            and (ue or {}).get('world') != 'simulate')
            if (stale_link or stale_camera) and editor_running():
                if stale_link and calls.start('link', START_LINK, lambda out: out.get('link', {}).get('running')):
                    restarted['link'] = now
                if stale_camera and calls.start('camera', START_CAMERA, lambda out: out.get('camera')):
                    restarted['camera'] = camera_advance = now
        time.sleep(.02)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
