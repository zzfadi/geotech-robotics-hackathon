"""Local festival animation preview; no actor transforms or Concert transactions.

Agent viewports may disable realtime. Advance only explicitly named Consciencia
performers from their serialized animation data, using wall time at at most 30 Hz.
Each editor evaluates its own skeletal pose. Runtime/PIE keeps normal playback.
"""
import time
import unreal

_handle = None
_next_scan = 0.0
_next_frame = 0.0
_performers = []
_heads = set()
_started = time.monotonic()
_cloud_mode_previous = None


def _cloud_preview(enable):
    """UE-198490 workaround, scoped to the festival preview rather than Avatar."""
    global _cloud_mode_previous
    name = 'r.VolumetricRenderTarget.Mode'
    if enable and _cloud_mode_previous is None:
        _cloud_mode_previous = unreal.SystemLibrary.get_console_variable_int_value(name)
        unreal.SystemLibrary.execute_console_command(None, name + ' 1')
    elif not enable and _cloud_mode_previous is not None:
        unreal.SystemLibrary.execute_console_command(None, name + ' ' + str(_cloud_mode_previous))
        _cloud_mode_previous = None


def tick(_dt):
    global _next_scan, _next_frame, _performers, _heads
    now = time.monotonic()
    if now < _next_frame:
        return
    _next_frame = now + 1.0 / 30.0
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if world is None or world.get_path_name() != '/Game/Maps/Tomorrowland.Tomorrowland':
        _cloud_preview(False)
        _performers = []
        _heads = set()
        _next_scan = 0.0
        return
    if now >= _next_scan:
        _next_scan = now + 1.0
        _performers = []
        actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
        # Concert can replace actor instances while keeping their labels. Drop
        # obsolete references so deleted fixtures do not accumulate indefinitely.
        _heads.intersection_update(actors)
        for actor in actors:
            label = actor.get_actor_label()
            if label.startswith('Consciencia_DMX_Head_') and actor not in _heads:
                try:
                    _opening_cue(actor, int(label.rsplit('_', 1)[1]) - 1)
                    _heads.add(actor)
                except Exception:
                    pass  # DMX assets may still be loading from Concert.
            if not (label.startswith('Consciencia_Dancer_') or label == 'Consciencia_DJ'):
                continue
            if not isinstance(actor, unreal.SkeletalMeshActor):
                continue
            component = actor.skeletal_mesh_component
            data = component.get_editor_property('animation_data')
            clip = data.anim_to_play
            if clip and clip.get_play_length() > 0:
                _performers.append((component, clip.get_play_length(), data.saved_position,
                                    data.saved_play_rate))
        _cloud_preview(bool(_performers))
    for component, duration, offset, rate in _performers:
        try:
            component.set_position(((now - _started) * rate + offset) % duration, False)
        except Exception:
            # Map reload invalidates old component references; reacquire next scan.
            _next_scan = 0.0


def _opening_cue(actor, index):
    # Apply once per loaded actor; incoming live DMX is free to override it.
    meshes = {c.get_name(): c for c in actor.get_components_by_class(unreal.StaticMeshComponent)}
    actor.initialize_fixture(meshes['StaticMeshLens'], meshes['StaticMeshBeam'])
    values = {'Dimmer': .85, 'Pan': .42 + .16 * index / 15,
              'Tilt': .25 if index < 8 else .33, 'Color': .18 if index % 2 == 0 else .65,
              'Gobo': 0., 'Zoom': .12, 'Shutter': 1.}
    mapped = {}
    for name, value in values.items():
        attribute = unreal.DMXAttributeName()
        attribute.set_editor_property('name', name)
        mapped[attribute] = value
    actor.push_normalized_values_per_attribute(unreal.DMXNormalizedAttributeValueMap(map=mapped))
    actor.interpolate_dmx_components(10.)


def start():
    global _handle
    if _handle is None:
        _handle = unreal.register_slate_post_tick_callback(tick)


def stop():
    global _handle, _performers
    if _handle is not None:
        unreal.unregister_slate_post_tick_callback(_handle)
        _handle = None
    _performers = []
    _cloud_preview(False)
