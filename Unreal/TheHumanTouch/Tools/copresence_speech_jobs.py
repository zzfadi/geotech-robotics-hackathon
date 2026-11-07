"""Fixed local speech jobs shared by scoped MCP and the existing camera attachment."""
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data), encoding='utf-8')
    # Windows readers/antivirus may briefly hold the destination without delete sharing.
    for attempt in range(20):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.025)


def render(root, agent_slug, text, recipient):
    if os.name != 'nt':
        raise RuntimeError('Local Windows speech renderer required')
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError('Speech text must contain 1–4000 characters')
    root = Path(root)
    folder = root / 'Saved/CoPresenceSpeech/jobs' / agent_slug / uuid.uuid4().hex
    folder.mkdir(parents=True)
    write_json(folder / 'request.json', {'text': text, 'recipient': recipient})
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File',
                    str(root / 'Tools/copresence_speech.ps1'), '-RequestFile',
                    str(folder / 'request.json'), '-OutputDirectory', str(folder)],
                   check=True, capture_output=True, timeout=90)
    import wave
    with wave.open(str(folder / 'speech.wav'), 'rb') as audio:
        duration = audio.getnframes() / audio.getframerate()
    if not 0 < duration <= 120:
        raise ValueError('Speech duration must be between zero and 120 seconds')
    cues = json.loads((folder / 'visemes.json').read_text(encoding='utf-8-sig'))['visemes']
    return folder, [[c['time'], c['id'], c['duration']] for c in cues], duration


def wait_state(folder, wanted, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        path = Path(folder) / 'state.json'
        if path.exists():
            state = json.loads(path.read_text(encoding='utf-8'))
            if state['state'] == 'error':
                raise RuntimeError(state.get('error', 'Speech failed'))
            if state['state'] == wanted:
                return state
        time.sleep(.1)
    raise TimeoutError(f'Speech worker did not reach {wanted}')


def interrupt(root, agent_slug, call_id):
    """Cancel synthetic output only; no microphone capture or process termination."""
    queue = Path(root) / 'Saved/CoPresenceSpeech/jobs' / agent_slug
    queue.mkdir(parents=True, exist_ok=True)
    worker = queue / 'worker.json'
    if not worker.exists() or json.loads(worker.read_text()).get('call_id') != call_id:
        raise RuntimeError('Stop request does not match the active meeting')
    write_json(queue / 'interrupted.json', {'time': time.time(), 'call_id': call_id})
    for request in queue.glob('*/request.json'):
        (request.parent / 'cancel').touch()


def check_output_allowed(root, agent_slug):
    started = os.environ.get('COPRESENCE_REPLY_STARTED')
    if started and interrupted_since(root, agent_slug, float(started)):
        raise RuntimeError('Reply interrupted by a participant; end the task silently')


def interrupted_since(root, agent_slug, started):
    marker = Path(root) / 'Saved/CoPresenceSpeech/jobs' / agent_slug / 'interrupted.json'
    return marker.exists() and json.loads(marker.read_text())['time'] >= started
