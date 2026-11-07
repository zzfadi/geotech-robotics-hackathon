"""Live branded companion. Initialize only through scoped CoPresence tools.

Requires the companion and MaterialBillboard component prepared in the level.
"""
import math, time, textwrap, unreal, json
from pathlib import Path
from copresence import ghost_labels as G, hub_client as H
handle = None
error = None
last = {}
listening_checked = 0.0
listening = False
def initialize():
    from copresence import meet_build_camera, meet_pool_finishes
    meet_pool_finishes.restore()
    meet_build_camera.install()
    global actor, holder, mesh, bubble, anchor, rotation, scale, original_mesh, friendly, curious, started, handle, fill
    if handle is not None:
        cleanup()
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    actor = next(a for a in actors if a.get_actor_label() == 'GeoTechEyes_Companion')
    fill = next((a for a in actors if a.get_actor_label() == 'Build_CompanionFill'), None)
    holder = actor
    mesh = actor.get_component_by_class(unreal.StaticMeshComponent)
    anchor, rotation, scale = actor.get_actor_location(), actor.get_actor_rotation(), actor.get_actor_scale3d()
    original_mesh = mesh.static_mesh
    friendly = unreal.load_asset('/Game/Agent/FlyingEyes/Friendly/SM_GeoTechEyes_Friendly')
    curious = unreal.load_asset('/Game/Agent/FlyingEyes/Curious/SM_GeoTechEyes_Curious')
    sprite = next(c for c in actor.get_components_by_class(unreal.MaterialBillboardComponent) if c.get_name() == 'MaterialBillboard')
    sprite.set_absolute(False, True, True)
    G._load_shared()
    bubble = G.Bubble(unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world(), holder, sprite)
    bubble.set_tail(True)
    started = time.monotonic()
    handle = unreal.register_slate_post_tick_callback(tick)
def cleanup():
    global handle
    if handle is not None:
        unreal.unregister_slate_post_tick_callback(handle)
        handle = None
    if G.is_alive(actor):
        actor.set_actor_location(anchor, False, False)
        actor.set_actor_rotation(rotation, False)
        actor.set_actor_scale3d(scale)
        mesh.set_static_mesh(original_mesh)
    if G.is_alive(holder):
        bubble.sprite.set_visibility(False, True)
def tick(dt):
    global error, last, listening_checked, listening, anchor, rotation
    try:
        if not G.is_alive(actor) or not G.is_alive(holder):
            cleanup()
            return
        now = time.monotonic()
        t = now - started
        statuses, messages, poll_age, online = H.snapshot()
        status = statuses.get('Denys Codex')
        age = (status or {}).get('age', 0.0) + poll_age
        state = G.effective_state(status if online else None, age)
        target = curious if state in ('thinking', 'working', 'researching') else friendly
        if mesh.static_mesh != target:
            mesh.set_static_mesh(target)
        building = actor.get_world().get_path_name() == '/Game/Maps/Tomorrowland.Tomorrowland'
        if building:
            from copresence import tracking
            camera, view = tracking.camera_info()
            desired = camera + view.get_forward_vector()*300 - view.get_right_vector()*120 + unreal.Vector(0,0,70)
            here=actor.get_actor_location()
            anchor=here+(desired-here)*min(1.0,dt*4)
            rotation=unreal.Rotator(yaw=view.yaw+90,roll=-view.pitch)
            actor.set_actor_rotation(rotation,False)
            if fill is not None and G.is_alive(fill):
                fill.set_actor_location(anchor-view.get_forward_vector()*80,False,False)
        p = unreal.Vector(anchor.x + .5 * math.cos(t*.7), anchor.y + .8 * math.sin(t*.7), anchor.z + .6 * math.sin(t*1.1))
        actor.set_actor_location(p, False, False)
        phase = t % 5.2
        blink = 1.0 if phase >= .16 else max(.12, abs(phase-.08)/.08)
        actor.set_actor_scale3d(unreal.Vector(scale.x, scale.y, scale.z*blink))
        content = G.bubble_content('Denys Codex', status, age) if online and state != 'away' else None
        if state == 'thinking' and not (status or {}).get('step'):
            content = ('dots', ())
        elif content and content[0] != 'dots':
            raw = ' '.join(str((status or {}).get('step') or ' '.join(content[1])).split()) if state in ('working', 'error') else ' '.join(content[1])
            lines = textwrap.wrap(raw, 40)[:4]
            if len(textwrap.wrap(raw, 40)) > 4 and lines:
                lines[-1] = lines[-1][:37].rstrip() + '...'
            content = (content[0], tuple(lines))
        if now - listening_checked > .5:
            listening_checked = now
            try:
                info = json.loads((Path(unreal.Paths.project_saved_dir()) / 'CoPresenceLogs/meet-listening.json').read_text())
                listening = info.get('state') == 'listening' and 0 <= time.time()-info['updated'] < 5
            except (OSError, ValueError, KeyError, TypeError):
                listening = False
        if listening:
            content = ('text', ('Listening', 'Send request'))
        bubble.show(content, now)
        anim_scale = bubble.scale(now)
        if anim_scale:
            factor = min((.42 if building else .14) if content and content[0] != 'dots' else (.55 if building else .18), (100.0 if building else 32.0) / (bubble.body[0] + 2*G.FRAME_PAD))
            bubble.place(unreal.Vector(-1, 0, 8), rotation, factor*anim_scale[0], factor*anim_scale[1])
            bubble.sprite.set_world_location(unreal.Vector(p.x-1, p.y, p.z+(45 if building else 6)), False, False)
        last = {'state': state, 'age': age, 'online': online, 'content': content, 'position': (p.x,p.y,p.z)}
    except Exception as exc:
        error = repr(exc)
        cleanup()

# Only the designated agent editor installs this watcher at startup. It waits for
# the persisted avatar map and rebuilds transient text resources after a map load.
watch_handle = None
last_watch = 0.0

def install_when_ready():
    global watch_handle
    if watch_handle is None:
        watch_handle = unreal.register_slate_post_tick_callback(_watch_map)


def _watch_map(dt):
    global last_watch
    now = time.monotonic()
    if now - last_watch < 1:
        return
    last_watch = now
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if world is None or world.get_path_name() not in ('/Game/Maps/GeoVerseAvatar.GeoVerseAvatar','/Game/Maps/Tomorrowland.Tomorrowland'):
        if handle is not None:
            cleanup()
        return
    if handle is not None:
        return
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    matches = [a for a in actors if a.get_actor_label() == 'GeoTechEyes_Companion']
    if len(matches) != 1 or not matches[0].get_components_by_class(unreal.MaterialBillboardComponent):
        return
    initialize()


def shutdown():
    global watch_handle
    if watch_handle is not None:
        unreal.unregister_slate_post_tick_callback(watch_handle)
        watch_handle = None
    if handle is not None:
        cleanup()
