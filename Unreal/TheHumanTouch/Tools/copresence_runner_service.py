"""Keep a Mac project's chosen Agent Chat runner alive after its launcher exits."""
import argparse
import hashlib
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import time

# The first agent's editor serves its tools here; the runners of agents on other ports say theirs
DEFAULT_PORT = 8000


def wait_running(target, timeout=10):
    """Bootstrap acceptance is not proof that launchd started the runner."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = subprocess.run(['launchctl', 'print', target], capture_output=True, text=True)
        if state.returncode == 0 and 'state = running' in state.stdout:
            return
        if 'last exit code = 78' in state.stdout:
            raise RuntimeError('Agent runner could not start (launchd EX_CONFIG). Inspect launchd diagnostics; the chat runner is not ready.')
        time.sleep(0.5)
    raise TimeoutError('Agent runner has not started. Inspect launchd diagnostics before retrying.')


def wait_unloaded(target, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = subprocess.run(['launchctl', 'print', target], capture_output=True, text=True)
        if state.returncode != 0:
            return
        time.sleep(0.25)
    raise TimeoutError('Previous runner is still unloading; do not start a duplicate runner')


def configuration(project, brain, port=DEFAULT_PORT):
    project = Path(project).resolve()
    if brain not in ('claude', 'codex'):
        raise ValueError('Choose claude or codex')
    label = 'one.geotech.copresence.runner.' + hashlib.sha256(str(project).encode()).hexdigest()[:12]
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('uv is required for the agent runner')
    arguments = [uv, 'run', '--quiet', '--script', str(project / 'Tools/copresence_mcp.py'), 'run-agent', brain]
    # An agent after the first on this Mac has its editor on a port of its own, and a runner of its own
    if port != DEFAULT_PORT:
        label += f'.{port}'
        arguments += ['--port', str(port)]
    # launchd opens stdout before starting uv. Service logs belong under Library,
    # not in the Documents project where macOS protects access by application.
    log = str(Path.home() / 'Library/Logs/CoPresence' / (label + '.log'))
    host_file = project / 'Saved/CoPresenceLogs/host_address'
    host = host_file.read_text().strip() if host_file.exists() else '127.0.0.1'
    hub = os.environ.get('COPRESENCE_HUB_URL') or f'http://{host}:8790'
    return {'Label': label, 'ProgramArguments': arguments,
            'WorkingDirectory': str(project), 'RunAtLoad': True,
            'KeepAlive': {'SuccessfulExit': False}, 'ThrottleInterval': 10,
            'EnvironmentVariables': {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                                     'COPRESENCE_HUB_URL': hub,
                                     'HUMANTOUCH_SHARED_EDITOR': '1',
                                     # The model the agent was started with, empty for the account's default
                                     'COPRESENCE_AGENT_MODEL': os.environ.get('COPRESENCE_AGENT_MODEL', ''),
                                     'COPRESENCE_AGENT_EFFORT': os.environ.get('COPRESENCE_AGENT_EFFORT', '')},
            'StandardOutPath': log, 'StandardErrorPath': log}


def start(project, brain, port=DEFAULT_PORT):
    config = configuration(project, brain, port)
    path = Path.home() / 'Library/LaunchAgents' / (config['Label'] + '.plist')
    domain = f'gui/{os.getuid()}'
    target = domain + '/' + config['Label']
    existing = plistlib.loads(path.read_bytes()) if path.exists() else None
    state = subprocess.run(['launchctl', 'print', target], capture_output=True, text=True)
    # uv prepends its transient environment to PATH for each caller. That is not
    # a runner identity change and must not restart an already active brain.
    identity = ('Label', 'ProgramArguments', 'WorkingDirectory', 'StandardOutPath', 'StandardErrorPath')
    same_runner = existing is not None and all(existing.get(key) == config.get(key) for key in identity)
    # The runner binds its HTTP client once. A solo/shared hub change needs a
    # restart even when the project and selected brain are unchanged.
    same_runner = same_runner and existing.get('EnvironmentVariables', {}).get('COPRESENCE_HUB_URL') == config['EnvironmentVariables']['COPRESENCE_HUB_URL']
    for setting in ('COPRESENCE_AGENT_MODEL', 'COPRESENCE_AGENT_EFFORT'):
        same_runner = same_runner and existing.get('EnvironmentVariables', {}).get(setting, '') == config['EnvironmentVariables'][setting]
    if same_runner and state.returncode == 0 and 'state = running' in state.stdout:
        return
    if state.returncode == 0:
        subprocess.run(['launchctl', 'bootout', target], check=True)
        wait_unloaded(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    log = Path(config['StandardOutPath'])
    log.parent.mkdir(parents=True, exist_ok=True)
    log.touch(mode=0o600, exist_ok=True)
    path.write_bytes(plistlib.dumps(config))
    path.chmod(0o600)
    subprocess.run(['launchctl', 'bootstrap', domain, str(path)], check=True)
    wait_running(target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    parser.add_argument('--brain', required=True, choices=['claude', 'codex'])
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help="The agent editor's MCP port")
    args = parser.parse_args()
    start(args.project, args.brain, args.port)
