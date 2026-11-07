"""Host-specific process plumbing for the same Meet service on Mac and Windows."""
from contextlib import contextmanager
from pathlib import Path
import os
import subprocess
import sys
import time


def wait_editor_session(read_session, timeout=300, interval=2, expected_session=None):
    """Wait for the launched editor, without restarting it on a slow startup."""
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            live = read_session()
            if live.get('agent') and (expected_session is None or live.get('session') == expected_session):
                return live
        except Exception as error:
            last_error = error
        time.sleep(interval)
    raise TimeoutError('Editor is still starting or unreachable; inspect its process/log before retrying') from last_error


@contextmanager
def service_lock(path):
    """Nonblocking OS lock, released even if a service crashes."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def bootstrap_command(project, platform=sys.platform):
    tools=Path(project)/'Tools'
    if platform=='win32':
        return ['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(tools/'meet-bootstrap.ps1')]
    if platform=='darwin':
        return ['bash',str(tools/'meet-bootstrap-mac.sh')]
    raise RuntimeError('Meet hosting supports macOS and Windows')


def chrome_path(platform=sys.platform):
    if platform=='darwin':
        return Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    if platform=='win32':
        return Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
    raise RuntimeError('Unsupported browser host')


def chrome_launch_command(profile, arguments, platform=sys.platform):
    browser_args = [f'--user-data-dir={Path(profile)}',
                    '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=9223',
                    *arguments, 'about:blank']
    if platform == 'darwin':
        # Register the separate profile with Launch Services so its sign-in
        # window is a normal foreground Mac application, not an orphan process.
        return ['open', '-n', '-a', '/Applications/Google Chrome.app', '--args', *browser_args]
    return [str(chrome_path(platform)), *browser_args]


def start_local_service(project):
    """The service lock prevents duplicate ownership. Logs stay local."""
    project=Path(project);logs=project/'Saved/CoPresenceLogs';logs.mkdir(parents=True,exist_ok=True)
    with (logs/'meeting-service.log').open('ab') as output:
        return subprocess.Popen(['uv','run','--script',str(project/'Tools/copresence_meeting_service.py')],
            cwd=project,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)


def launch_settings(request, platform=sys.platform):
    """Validate the small configuration surface accepted by either host service."""
    mode=request.get('mode','multiuser')
    brain=request.get('brain','claude' if platform=='win32' else 'codex')
    if mode not in ('solo','multiuser') or brain not in ('claude','codex'):
        raise ValueError('Invalid connection mode or agent brain')
    session=request.get('session','MultiuserAI_UE')
    import re
    if not isinstance(session,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',session):
        raise ValueError('Invalid session name')
    return {'COPRESENCE_CONNECTION_MODE':mode,'COPRESENCE_AGENT_BRAIN':brain,
            'COPRESENCE_SESSION_NAME':session}


def verify_editor(request, live, platform=sys.platform):
    settings=launch_settings(request,platform)
    mode=settings['COPRESENCE_CONNECTION_MODE']
    actual=live.get('mode','multiuser')
    if actual!=mode:
        raise RuntimeError('Editor topology differs from the requested launch; save and close it first')
    if mode=='multiuser' and live.get('session')!=settings['COPRESENCE_SESSION_NAME']:
        raise RuntimeError('Editor has not joined the requested Multi-User session yet')
    if not live.get('agent'):
        raise RuntimeError('Editor agent identity is unavailable')


def start_panel_supervisor(project, credential_file=None):
    """Start under the service interpreter so relay dependencies match on both OSes."""
    project=Path(project)
    credential=Path(credential_file) if credential_file else Path.home()/'.copresence-private/panel-host.credential'
    if not credential.is_file():
        raise RuntimeError('Provision the private add-on host credential before starting a call')
    logs=project/'Saved/CoPresenceLogs';logs.mkdir(parents=True,exist_ok=True)
    with (logs/'panel-supervisor.log').open('ab') as output:
        return subprocess.Popen([sys.executable,'-u',str(project/'Tools/copresence_panel_supervisor.py'),
            '--project',str(project),'--credential-file',str(credential)],cwd=project,
            stdout=output,stderr=subprocess.STDOUT,start_new_session=os.name!='nt')
