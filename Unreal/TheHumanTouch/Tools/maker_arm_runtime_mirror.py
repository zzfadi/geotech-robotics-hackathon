"""Measured SIE transforms -> Concert editor snapshots; never runs physics.

Experimental, opt-in: caller owns runtime/controller and one active CoPresence task.
Construct after enclosing MCP transaction ends; finish BEFORE ending SIE/task.
"""
import math
import time
import unreal
from copresence import animation, tracking

_active = None


def _values(t):
    p, q, s = t.translation, t.rotation, t.scale3d
    return (p.x, p.y, p.z, q.x, q.y, q.z, q.w, s.x, s.y, s.z)


def _same(a, b):
    av, bv = _values(a), _values(b)
    # Quaternion sign is immaterial.
    return (max(abs(av[i]-bv[i]) for i in (0,1,2,7,8,9)) < 1e-4
            and abs(sum(av[i]*bv[i] for i in (3,4,5,6))) > 1-1e-8)


def _resolve(world, labels):
    found = {label: [] for label in labels}
    for actor in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor):
        label = actor.get_actor_label()
        if label in found:
            found[label].append(actor)
    invalid = {label: len(actors) for label, actors in found.items() if len(actors) != 1}
    if invalid:
        raise ValueError('Missing or duplicate mirror actor labels in ' +
                         (world.get_path_name() if world is not None else '<None>') + ': ' + repr(invalid))
    return {label: actors[0] for label, actors in found.items()}


class RuntimeMirror:
    """One bounded transaction; finish(False) restores owned editor poses.

    tick() may raise: controller must stop motion and call finish(False).
    Watchdog closes on timeout, task change, or runtime teardown. Check closed
    before advancing physics. No implicit commit, package save, or peer physics.
    """
    def __init__(self, world, labels, max_seconds=120, hz=10, editor_world=None):
        global _active
        labels = list(labels)
        if not labels or len(set(labels)) != len(labels):
            raise ValueError('Provide nonempty unique labels')
        if not 0 < max_seconds <= 180 or not 0 < hz <= 20:
            raise ValueError('Mirror bound: <=180 seconds, <=20 Hz')
        if (_active and not _active.closed) or animation.is_running():
            raise RuntimeError('Another animation/mirror is active')
        self.editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        if world is None or world != self.editor.get_game_world():
            raise ValueError('Mirror requires the current authoritative game world')
        self.task_id = tracking.current_task['id']
        if not self.task_id:
            raise RuntimeError('Start a tracked CoPresence task first')
        self.world = world
        self.editor_world = editor_world if editor_world is not None else self.editor.get_editor_world()
        if (self.editor_world is None or self.editor_world == world
                or not unreal.SystemLibrary.is_valid(self.editor_world)):
            raise ValueError('Cache a valid editor world before starting SIE')
        self.runtime = _resolve(world, labels)
        self.targets = _resolve(self.editor_world, labels)
        self.original = {k: a.get_actor_transform() for k,a in self.targets.items()}
        self.expected = dict(self.original)
        self.deadline = time.monotonic() + max_seconds
        self.period = 1/hz
        self.last = -math.inf
        self.closed = False
        self.error = None
        self.callback = None
        self.undo_before = tracking.undo_count()
        index = unreal.SystemLibrary.begin_transaction('CoPresence', unreal.Text(
            tracking.transaction_title('Measured runtime Maker Arm motion')), next(iter(self.targets.values())))
        if index != 0:
            if index > 0:
                unreal.SystemLibrary.end_transaction()
            raise RuntimeError('Construct mirror after enclosing transaction ends')
        try:
            for actor in self.targets.values():
                unreal.SystemLibrary.transact_object(actor)
                unreal.SystemLibrary.transact_object(actor.root_component)
            self.callback = unreal.register_slate_post_tick_callback(self._watchdog)
        except Exception:
            unreal.SystemLibrary.end_transaction()
            self.closed = True
            raise
        _active = self

    def _valid(self):
        if self.closed:
            raise RuntimeError(self.error or 'Mirror is closed')
        if time.monotonic() >= self.deadline:
            raise RuntimeError('Runtime mirror deadline reached')
        if tracking.current_task['id'] != self.task_id:
            raise RuntimeError('Owning task changed')
        current_editor = self.editor.get_editor_world()
        if (self.editor.get_game_world() != self.world
                or not unreal.SystemLibrary.is_valid(self.editor_world)
                or (current_editor is not None and current_editor != self.editor_world)):
            raise RuntimeError('Mirror world changed')

    def _watchdog(self, delta):
        try:
            self._valid()
        except Exception as error:
            self.error = str(error)
            self.finish(commit=False)

    def tick(self, force=False):
        try:
            self._valid()
            now = time.monotonic()
            if not force and now-self.last < self.period:
                return False
            measured = {}
            for key, actor in self.runtime.items():
                target = self.targets[key]
                if not all(unreal.SystemLibrary.is_valid(a) for a in (actor,target)):
                    raise RuntimeError('Mirror actor disappeared')
                if not _same(target.get_actor_transform(), self.expected[key]):
                    raise RuntimeError('Concurrent editor transform change: '+key)
                pose = actor.get_actor_transform()
                if not all(math.isfinite(v) for v in _values(pose)):
                    raise RuntimeError('Nonfinite runtime pose: '+key)
                measured[key] = pose
            # Preflight all pairs before writing any of this sample.
            for key, pose in measured.items():
                if not _same(pose, self.expected[key]):
                    actor = self.targets[key]
                    actor.set_actor_transform(pose, False, True)
                    self.expected[key] = actor.get_actor_transform()
                    unreal.SystemLibrary.snapshot_object(actor.root_component)
            self.last = now
            return True
        except Exception as error:
            self.error = str(error)
            self.finish(commit=False)
            raise

    def finish(self, commit=False):
        if self.closed:
            return
        if commit:
            # Capture the final measured pose even inside the rate-limit interval.
            # A failed final validation aborts/restores through tick(), never commits.
            self.tick(force=True)
        self.closed = True
        try:
            if self.callback is not None:
                unreal.unregister_slate_post_tick_callback(self.callback)
                self.callback = None
            if not commit:
                for key, actor in self.targets.items():
                    if not unreal.SystemLibrary.is_valid(actor):
                        continue
                    # Never restore over a concurrent human/editor modification.
                    if _same(actor.get_actor_transform(), self.expected[key]):
                        if not _same(self.original[key], self.expected[key]):
                            actor.set_actor_transform(self.original[key], False, True)
                            unreal.SystemLibrary.snapshot_object(actor.root_component)
        finally:
            # Explicit restored snapshots reach Concert; cancelling could hide them.
            unreal.SystemLibrary.end_transaction()
            if tracking.current_task['id'] == self.task_id:
                tracking.current_task['transactions'] += tracking.undo_count()-self.undo_before
