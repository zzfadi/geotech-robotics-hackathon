"""Read-only, bounded transform sampling for comparing Multi-User editors.

No actor, viewport, session, package, or transaction changes. Output belongs in
ignored Saved/MultiUserAudit. Run the same sampler on two connected editors,
then compare final poses and the presence of intermediate poses independently.
"""
import json
import time
from pathlib import Path
import unreal

_active = None


class MotionAudit:
    def __init__(self, directory, seconds=60, fps=10):
        if not 0 < seconds <= 120 or not 0 < fps <= 20:
            raise ValueError('Audit duration must be <=120 seconds and rate <=20 Hz')
        self.directory = Path(directory)
        if not self.directory.is_absolute():
            self.directory = Path(unreal.Paths.project_dir()) / self.directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.started = time.time()
        self.deadline = time.monotonic() + seconds
        self.interval = 1 / fps
        self.last = 0
        self.frames = 0
        self.error = None
        self.output = (self.directory / 'transforms.jsonl').open('w')
        self.handle = unreal.register_slate_post_tick_callback(self.tick)

    def tick(self, delta):
        now = time.monotonic()
        if now >= self.deadline:
            self.stop()
            return
        if now - self.last < self.interval:
            return
        self.last = now
        try:
            actors = {}
            for actor in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
                label = actor.get_actor_label()
                if not (label.startswith('MakerArm_') or label.startswith('MakerCamera_')):
                    continue
                transform = actor.get_actor_transform()
                p, q = transform.translation, transform.rotation
                actors[label] = {'p': [p.x, p.y, p.z], 'q': [q.x, q.y, q.z, q.w]}
            world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
            self.output.write(json.dumps({'at': time.time(), 'map': world.get_path_name(), 'actors': actors}) + '\n')
            self.output.flush()
            self.frames += 1
        except Exception as error:
            self.error = str(error)
            self.stop()

    def stop(self):
        if self.handle is None:
            return
        unreal.unregister_slate_post_tick_callback(self.handle)
        self.handle = None
        self.output.close()
        (self.directory / 'status.json').write_text(json.dumps({
            'running': False, 'frames': self.frames, 'started': self.started,
            'ended': time.time(), 'error': self.error,
            'scope': 'read-only editor transform audit; no scene changes',
        }))


def start(directory, seconds=60, fps=10):
    global _active
    if _active and _active.handle is not None:
        raise RuntimeError('A motion audit is already active')
    _active = MotionAudit(directory, seconds, fps)
    return {'running': True, 'directory': str(_active.directory)}


def stop():
    if _active:
        _active.stop()
