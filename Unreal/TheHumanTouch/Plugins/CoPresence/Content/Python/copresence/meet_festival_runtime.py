"""Restart-safe playback for the saved GeoVerseBuild festival, with one owner.

Never spawns, saves or relocates actors. Stores paths, not UObject references, so
map unload is safe. A scoped operator can pause() before native human-host saves.
"""
import math
import sys
import unreal

MAP = '/Game/Maps/Tomorrowland'
ANIMATION = '/Game/Fab/Rumba_Dancing-_female_character/rumba_dancing_female_character/SkeletalMeshes/rumba_dancing_female_character_Anim'
DURATION = 2.3666665554
LIGHTS = tuple('Festival_MovingSpot_%d' % i for i in range(1, 5))
DANCERS = tuple('Festival_Dancer_%02d' % i for i in range(1, 13))
handle = None
paths = {}
elapsed = 0.0
retry_elapsed = 2.0
frames = 0
error = None
state = 'stopped'


def _world():
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world().get_path_name().split('.')[0]


def _owner():
    from copresence import agent_editor, tracking
    return agent_editor.is_agent_editor() and tracking.agent_name() == 'Denys Codex'


def pause():
    global handle, paths, state
    if handle is not None:
        unreal.unregister_slate_post_tick_callback(handle)
        handle = None
    paths = {}
    state = 'paused'
    # Retire the predecessor callbacks when migrating a live session too.
    for name in ('geoverse_stage_lights', 'geoverse_festival_crowd'):
        old = sys.modules.get(name)
        if old is not None and callable(getattr(old, 'cleanup', None)):
            old.cleanup()


def install_when_ready():
    global handle, state, error, retry_elapsed
    if not _owner():
        return snapshot()
    if handle is None:
        pause()
        error = None
        retry_elapsed = 2.0
        state = 'waiting'
        handle = unreal.register_slate_post_tick_callback(tick)
    return snapshot()


def _resolve():
    global paths, elapsed, state
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    found = {}
    for actor in actors:
        label = actor.get_actor_label()
        if label in LIGHTS + DANCERS:
            if label in found:
                raise RuntimeError('Duplicate festival actor label: ' + label)
            found[label] = actor.get_path_name()
    missing = set(LIGHTS + DANCERS) - set(found)
    if missing:
        state = 'waiting_for_saved_actors'
        return False
    animation = unreal.load_asset(ANIMATION)
    if animation is None:
        raise RuntimeError('Saved festival animation is unavailable')
    for i, label in enumerate(DANCERS):
        actor = unreal.find_object(None, found[label])
        component = actor.get_component_by_class(unreal.SkeletalMeshComponent)
        if component is None:
            raise RuntimeError('Missing dancer component: ' + label)
        component.set_update_animation_in_editor(True)
        component.override_animation_data(animation, True, True, (i * .19) % DURATION, 1.0)
    paths = found
    elapsed = 0.0
    state = 'running'
    return True


def tick(dt):
    global elapsed, retry_elapsed, frames, error, paths, state
    try:
        if _world() != MAP:
            paths = {}
            state = 'waiting_for_build'
            return
        if not paths:
            retry_elapsed += max(0.0, dt)
            if retry_elapsed < 2.0:
                return
            retry_elapsed = 0.0
            if not _resolve():
                return
        elapsed += max(0.0, dt)
        for i, label in enumerate(LIGHTS):
            actor = unreal.find_object(None, paths[label])
            if actor is None:
                paths = {}; state = 'waiting_for_saved_actors'; return
            actor.set_actor_rotation(unreal.Rotator(
                pitch=-48 + 23 * math.sin(elapsed * .7 + i * 1.4),
                yaw=-90 + 38 * math.sin(elapsed * .45 + i * 1.7), roll=0), False)
        for i, label in enumerate(DANCERS):
            actor = unreal.find_object(None, paths[label])
            if actor is None:
                paths = {}; state = 'waiting_for_saved_actors'; return
            component = actor.get_component_by_class(unreal.SkeletalMeshComponent)
            if component is None:
                raise RuntimeError('Missing dancer component: ' + label)
            component.set_position((elapsed + i * .19) % DURATION, False)
        frames += 1
    except Exception as exc:
        error = str(exc)
        pause()
        state = 'error'


def snapshot():
    return dict(state=state, active=handle is not None, frames=frames,
                lights=sum(label in paths for label in LIGHTS),
                dancers=sum(label in paths for label in DANCERS), error=error)
