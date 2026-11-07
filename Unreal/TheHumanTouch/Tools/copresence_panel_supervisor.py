"""Follow the host meeting with one guest relay; credentials stay in a private folder."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
from copresence_video_supervisor import target
from copresence_host_runtime import service_lock
from copresence_speech_jobs import write_json


def grant(cloud, credential, call):
    request = urllib.request.Request(cloud+'/api/meet-agent/host-session',
        json.dumps({'call': call}).encode(),
        {'Authorization': 'Bearer '+credential, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)['token']


class RelaySupervisor:
    def __init__(self, args, spawn=subprocess.Popen, issue=grant):
        self.args, self.spawn, self.issue = args, spawn, issue
        self.root = args.project/'Saved/CoPresenceLogs'
        self.root.mkdir(parents=True, exist_ok=True)
        self.child = self.current = self.output = self.token_file = None
        self.last_call = None
        self.agent = None
        self.hub_url = None
        self.retry_at = 0

    def _previous_audio_preference(self, current_call):
        candidates = []
        if self.last_call and self.last_call != current_call:
            p = self.root/('meet-panel-'+self.last_call+'.json')
            if p.exists():
                candidates.append(p)
        candidates.extend(sorted(
            (p for p in self.root.glob('meet-panel-*.json') if p.name != 'meet-panel-'+current_call+'.json' and p not in candidates),
            key=lambda p: p.stat().st_mtime, reverse=True))
        for p in candidates:
            try:
                data = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and isinstance(data.get('audioReplies'), bool):
                return data['audioReplies']
        return None

    def stop(self):
        if self.child and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=5)
        self.child = self.current = None
        if self.output:
            self.output.close()
            self.output = None
        if self.token_file:
            self.token_file.unlink(missing_ok=True)
            self.token_file = None

    def tick(self, state, now):
        url = target(state, now)
        agent = state.get("agent")
        hub_url = state.get('hub_url', self.args.hub_url)
        if not isinstance(agent, str) or not agent.strip():
            url = None
        if self.child and (url != self.current or agent != self.agent or hub_url != self.hub_url or self.child.poll() is not None):
            changed = url != self.current or agent != self.agent or hub_url != self.hub_url
            self.stop()
            if changed:
                self.retry_at = 0
        if not url or self.child or now < self.retry_at:
            return
        self.retry_at = now+15
        call = url.rsplit('/', 1)[-1]
        token = self.issue(self.args.cloud, self.args.credential_file.read_text().strip(), call)
        # Inherit the provisioned credential directory's Windows ACL; never put
        # grants in project logs or command arguments. POSIX files use mode 0600.
        self.token_file = self.args.credential_file.parent/('panel-'+call+'.token')
        try:
            fd = os.open(self.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(token)
            state_file = self.root/('meet-panel-'+call+'.json')
            if not state_file.exists():
                audio = self._previous_audio_preference(call)
                if audio is not None:
                    write_json(state_file, {'active': None, 'call': call, 'pending': [], 'audioReplies': audio})
            self.output = (self.root/('meet-panel-'+call+'.log')).open('ab')
            self.child = self.spawn([
                sys.executable, '-u', str(Path(__file__).with_name('copresence_meet_panel_relay.py')),
                '--cloud', self.args.cloud, '--call', call, '--project', str(self.args.project),
                '--hub-url', hub_url, '--token-file', str(self.token_file),
                '--state-file', str(self.root/('meet-panel-'+call+'.json')),
                '--code-file', str(self.root/'hub_code'), '--agent', agent, '--guest-access'],
                cwd=self.args.project, stdout=self.output, stderr=subprocess.STDOUT)
            self.current = url
            self.agent = agent
            self.hub_url = hub_url
            self.last_call = call
            print('Panel relay attached to '+call, flush=True)
        except Exception:
            self.stop()
            raise


def run(args):
    supervisor = RelaySupervisor(args)
    with service_lock(supervisor.root/'panel-supervisor.lock'):
        try:
            while True:
                try:
                    state = json.loads((args.project/'Saved/CoPresenceMeet/status.json').read_text())
                except (OSError, ValueError):
                    state = {}  # Missing/corrupt service status must detach the old call.
                try:
                    supervisor.tick(state, time.time())
                except Exception as error:
                    print('Panel supervisor retry: '+type(error).__name__, flush=True)
                write_json(supervisor.root/'panel-supervisor-status.json', dict(
                    updated=time.time(), call=supervisor.current,
                    relay_pid=supervisor.child.pid if supervisor.child else None))
                time.sleep(3)
        finally:
            supervisor.stop()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--credential-file', type=Path, required=True)
    p.add_argument('--cloud', default='https://geotech-workspace-addon.web.app',
                   choices=['https://geotech-workspace-addon.web.app'])
    p.add_argument('--hub-url', default='http://100.84.28.59:8790')
    run(p.parse_args())
