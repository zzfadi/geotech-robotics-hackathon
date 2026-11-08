#!/usr/bin/env python3
"""Mac-side Table 10 agent: live camera upload, test-button relay and link health.

- Keeps Tools/maker_arm_real_link.py (Table 10 <-> real arms) and the live Table 10 camera
  (Tools/maker_arm_table10_camera.py) running in the local editor, restarting them if they stall.
- Uploads every new Table 10 frame (up to 10 fps) to the web page.
- Relays the web page's test buttons (move_up / idle / stop) to the link via
  Saved/RealArmLink/command.json and reports link, bridge and sync status back.
- Starts and supervises the headless real-arm service (Tools/real_arm_service.py, maker-arm-sdk
  venv) while the editor runs, and turns the web page's drive request into the service's
  heartbeat (control.json). No heartbeat -> the service returns the arms and releases torque.

    python3 DreamSim/table10_agent.py [--server http://100.84.28.59:8786]
"""
import argparse
import base64
import json
import math
import subprocess
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
SERVER_LOST_S = 2.  # drive heartbeat stops when the web server has not answered for this long

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


def editor_running():
    return subprocess.run(['pgrep', '-f', 'UnrealEditor.app/Contents/MacOS/UnrealEditor'], capture_output=True).returncode == 0


def interactive_bridge_running():
    return subprocess.run(['pgrep', '-f', 'real_arm_bridge.py'], capture_output=True).returncode == 0


class RealArmService:
    """The headless real-arm service process (holds both USB-CAN adapters while running)."""
    def __init__(self):
        self.process = None
        self.started = 0.
        self.editor_gone = None

    def running(self):
        # Also adopts a service left by a previous agent run (it holds the adapters).
        if self.process is not None and self.process.poll() is None:
            return True
        return subprocess.run(['pgrep', '-f', 'real_arm_service.py'], capture_output=True).returncode == 0

    def supervise(self, now):
        if editor_running():
            self.editor_gone = None
            if (not self.running() and now-self.started > RESTART_EVERY_S and SDK_PYTHON.exists()
                    and not interactive_bridge_running()):
                self.started = now
                log = open(LINK_DIR/'service.log', 'a')
                self.process = subprocess.Popen(
                    [str(SDK_PYTHON), '-u', 'real_arm_service.py', '--left', ADAPTERS['left'], '--right', ADAPTERS['right'],
                     '--state-dir', str(LINK_DIR)], cwd=TOOLS, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
                print('started real-arm service (pid %d)' % self.process.pid, flush=True)
        else:
            self.editor_gone = self.editor_gone or now
            if self.running() and now-self.editor_gone > 30:
                self.stop('editor closed')

    def stop(self, why):
        if self.running():
            print('stopping real-arm service: %s' % why, flush=True)
            subprocess.run(['pkill', '-TERM', '-f', 'real_arm_service.py'])  # verified return + torque off, exit
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
    service = RealArmService()
    drive = {'on': False, 'seq': 0}
    release_seq = 0
    server_ok = 0.
    last_frame_seq = None
    camera_advance = time.monotonic()
    claimed = None
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
            real, ue = read_json(LINK_DIR/'real_state.json'), read_json(LINK_DIR/'ue_state.json')
            real_age = time.time()-(real or {}).get('unix', 0)
            ue_age = time.time()-(ue or {}).get('unix', 0)
            report = {
                'link': {'mode': (ue or {}).get('mode'), 'age_s': round(ue_age, 2), 'test': (ue or {}).get('test'),
                         'world': (ue or {}).get('world'),
                         'q_deg': {s: [round(math.degrees(v), 1) for v in a.get('q', [])] for s, a in ((ue or {}).get('arms') or {}).items()}},
                'bridge': {'mode': (real or {}).get('mode'), 'age_s': round(real_age, 2),
                           'q_deg': {s: [round(math.degrees(v), 1) for v in a.get('q', [])] for s, a in ((real or {}).get('arms') or {}).items()}},
                'sync_deg': sync_deg(real, ue) if real_age < 1 and ue_age < 1 else {},
                'camera': {'running': bool(info.get('running')), 'sequence': seq, 'error': info.get('error'),
                           'age_s': round(time.time()-info.get('captured_unix', 0), 2) if info else None},
                'claimed': claimed}
            status = read_json(LINK_DIR/'service.json') or {}
            report['service'] = {'running': service.running() and time.time()-status.get('unix', 0) < 3,
                                 'mode': status.get('mode'), 'message': status.get('message'),
                                 'interactive_bridge': interactive_bridge_running() if not service.running() else False}
            try:
                reply = server.post('/api/table10/agent', report)
                server_ok = now
                drive = reply.get('drive') or drive
                release_seq = reply.get('release_seq', release_seq)
                command = reply.get('command')
                if command and command.get('id') != claimed:
                    write_json(LINK_DIR/'command.json', {'id': command['id'], 'action': command['action'], 'unix': time.time()})
                    claimed = command['id']
                    print('test command: %s (%s)' % (command['action'], command['id'][:8]), flush=True)
            except (OSError, ValueError) as error:
                print('heartbeat: %s' % error, flush=True)
            # Drive heartbeat for the real-arm service: only while the server answers.
            write_json(LINK_DIR/'control.json', {'unix': time.time(), 'source': 'web',
                                                  'drive': bool(drive.get('on')) and now-server_ok < SERVER_LOST_S,
                                                  'drive_seq': drive.get('seq'), 'release_seq': release_seq,
                                                  'sides': drive.get('sides') or ['left', 'right']})
            # A drive request the service could not engage within its window: clear it so the
            # page shows the refusal and Enable drive can be pressed again.
            if drive.get('on') and status.get('mode') == 'mirror' and drive.get('since') and time.time()-drive['since'] > 13:
                try:
                    server.post('/api/table10/test', {'action': 'drive_off', 'source': 'engage failed'})
                    print('drive request cleared: not engaged (%s)' % status.get('message'), flush=True)
                    drive = {**drive, 'on': False}
                except (OSError, ValueError) as error:
                    print('drive clear: %s' % error, flush=True)
            service.supervise(now)
            # Restart the link or camera when they stop advancing (e.g. after an editor restart).
            if ue_age > 3 and now-restarted['link'] > RESTART_EVERY_S:
                restarted['link'] = now
                try:
                    print('starting Table 10 link: %s' % editor(START_LINK).get('link', {}).get('running'), flush=True)
                except Exception as error:
                    print('link start failed: %s' % str(error)[-200:], flush=True)
            if now-camera_advance > 5 and now-restarted['camera'] > RESTART_EVERY_S and (ue or {}).get('world') != 'simulate':
                restarted['camera'] = now
                try:
                    print('starting Table 10 camera: %s' % editor(START_CAMERA).get('camera'), flush=True)
                    camera_advance = now
                except Exception as error:
                    print('camera start failed: %s' % str(error)[-200:], flush=True)
        time.sleep(.02)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
