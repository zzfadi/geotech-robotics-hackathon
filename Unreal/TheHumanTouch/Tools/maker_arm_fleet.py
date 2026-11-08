"""Replica Maker Arm stations: the shared hall extended by one booth per table.

Booth k (1..12) is an exact copy of the original hall + station, shifted
k x 8 m along +Y. Booths share walls: each booth's left wall is the previous
booth's right wall (the original hall's right wall for booth 1), so the hall
becomes one long room of identical 8 m bays. Every camera sees the same walls,
windows and lighting as the original table, and no camera sees another table.

Shared mode (default) creates normal, replicated, persisted actors through the
caller's CoPresence run_python transaction: call build_table() once per table
(one bounded transaction each), then reset() before every fleet run.
local=True instead makes editor-only scratch copies in a cancelled transaction.
Table 0 reuses the original station without cloning or tagging it.
"""
import json
from pathlib import Path

import unreal
from maker_arm_model import mounted_frames, ue_pose
from maker_arm_station import BASES, MOUNT_YAW, VISUAL_LINKS
from maker_arm_fleet_layout import (FLEET_TAG, label, offset, replica_base, parse_label, table_tag,
                                    normalize_config, is_source_label)

GLOBAL_CLASSES = ('DirectionalLight', 'SkyLight', 'PostProcessVolume', 'ExponentialHeightFog',
                  'SkyAtmosphere', 'VolumetricCloud')
CAMERA_ROLES = {'overhead': 'MakerCamera_Overview', 'wrist_l': 'MakerCamera_LeftWrist',
                'wrist_r': 'MakerCamera_RightWrist'}


def _subsystem():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def _local_tag():
    from copresence import tracking
    return tracking.LOCAL_ONLY_TAG


def _actors():
    return {a.get_actor_label(): a for a in _subsystem().get_all_level_actors()}


def _tags(actor):
    return {str(t) for t in actor.tags}


def sources():
    out = {}
    skip = {_local_tag(), FLEET_TAG}
    for actor in _subsystem().get_all_level_actors():
        name = actor.get_actor_label()
        if is_source_label(name) and actor.get_class().get_name() not in GLOBAL_CLASSES and not (_tags(actor) & skip):
            out[name] = actor
    return out


def fleet_actors(index=None):
    if index == 0:
        return list(sources().values())
    tag = FLEET_TAG if index is None else table_tag(index)
    return [a for a in _subsystem().get_all_level_actors() if tag in _tags(a)]


def _check_idle():
    if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world() is not None:
        raise RuntimeError('A simulation is running; fleet scene edits only run while the editor is idle')
    from copresence import animation
    if animation.is_running():
        raise RuntimeError('A shared animation is running; fleet scene edits only run while the editor is idle')


def _locally(make):
    from copresence_call import call_camera
    result = call_camera._locally(make)
    if result is None:
        raise RuntimeError('Run local fleet setup after the enclosing transaction ends')
    return result


def _shift(transform, delta):
    p = transform.translation
    return unreal.Transform(unreal.Vector(p.x + delta[0], p.y + delta[1], p.z + delta[2]),
                            transform.rotation.rotator(), transform.scale3d)


def _close(a, b, tol=1e-3):
    return all(abs(x-y) <= tol for x, y in zip(a, b))


def set_pose(index, side, q, gap, existing=None):
    """Kinematic pose for one replica arm; wrist cameras follow via attachment.

    Returns the number of links actually moved (no-op writes are skipped)."""
    if side not in BASES:
        raise ValueError('Unknown arm')
    if not 0 <= gap <= .104825:
        raise ValueError('Jaw gap outside 0..0.104825 m')
    existing = existing or _actors()
    base = replica_base(index, BASES[side])
    moved = 0
    for link, t in mounted_frames(q, gap, yaw=MOUNT_YAW[side]).items():
        if link not in VISUAL_LINKS:
            continue
        actor = existing.get(label(index, 'MakerArm_'+side+'_'+link))
        if actor is None:
            raise ValueError('Missing replica link %s on table %d' % (link, index))
        p, r = ue_pose(t)
        p = [p[i]+base[i] for i in range(3)]
        now = actor.get_actor_transform()
        q_now = now.rotation
        if (_close(p, (now.translation.x, now.translation.y, now.translation.z), .001)
                and abs(sum(a*b for a, b in zip((q_now.x, q_now.y, q_now.z, q_now.w), r))) > 1-1e-9):
            continue
        actor.modify()
        # Set the exact quaternion: a rotator round trip near +/-90 deg pitch misses verify()'s
        # 1e-6 dot tolerance ("robot rotated outside the controller" on the next start).
        pose = actor.get_actor_transform()
        pose.translation = unreal.Vector(*p)
        pose.rotation = unreal.Quat(*r)
        actor.set_actor_transform(pose, False, True)
        moved += 1
    return moved


def verify(index, state, actors):
    """Same tolerance as maker_arm_feedback.verify, for one replica table."""
    for side in BASES:
        base = replica_base(index, BASES[side])
        for link, t in mounted_frames(state['q_'+side], state['gap_'+side+'_m'], yaw=MOUNT_YAW[side]).items():
            if link not in VISUAL_LINKS:
                continue
            actor = actors.get(label(index, 'MakerArm_'+side+'_'+link))
            if actor is None:
                raise ValueError('Missing replica link on table %d' % index)
            p, r = ue_pose(t)
            actual = actor.get_actor_transform()
            position, rotation = actual.translation, actual.rotation
            if max(abs(a-(p[i]+base[i])) for i, a in enumerate((position.x, position.y, position.z))) > .01:
                raise ValueError('Table %d robot moved outside the controller' % index)
            if abs(sum(a*b for a, b in zip((rotation.x, rotation.y, rotation.z, rotation.w), r))) < .999999:
                raise ValueError('Table %d robot rotated outside the controller' % index)


def _place_target(index, variant, existing):
    spec = variant['target']
    actor = existing.get(label(index, spec['label']))
    if actor is None:
        raise ValueError('Table %d has no replica of %s' % (index, spec['label']))
    d = offset(index)
    here = actor.get_actor_location()
    loc = ([spec['location_cm'][i] + d[i] for i in range(3)] if spec.get('location_cm') is not None
           else [here.x, here.y, here.z])
    loc = [loc[i] + spec['offset_cm'][i] for i in range(3)]
    rot = actor.get_actor_rotation()
    rotation = unreal.Rotator(pitch=rot.pitch, yaw=spec['yaw_deg'], roll=rot.roll) if spec.get('yaw_deg') is not None else rot
    if spec.get('rotation_deg') is not None:
        pitch, yaw, roll = spec['rotation_deg']
        rotation = unreal.Rotator(pitch=pitch, yaw=yaw, roll=roll)
    scale = actor.get_actor_scale3d()
    want_scale = spec.get('scale') or [scale.x, scale.y, scale.z]
    unchanged = (_close(loc, (here.x, here.y, here.z)) and _close(want_scale, (scale.x, scale.y, scale.z), 1e-6)
                 and _close((rotation.pitch, rotation.yaw, rotation.roll), (rot.pitch, rot.yaw, rot.roll), 1e-3))
    if not unchanged:
        actor.modify()
        actor.set_actor_location_and_rotation(unreal.Vector(*loc), rotation, False, True)
        actor.set_actor_scale3d(unreal.Vector(*want_scale))
    return loc, not unchanged


def _duplicate_table(index, source, local):
    if index == 0:
        raise ValueError('Original table must never be duplicated')
    subsystem = _subsystem()
    keep = unreal.AttachmentRule.KEEP_WORLD
    detach = unreal.DetachmentRule.KEEP_WORLD
    tags = ([unreal.Name(_local_tag())] if local else []) + [unreal.Name(FLEET_TAG), unreal.Name(table_tag(index))]
    for old in fleet_actors(index):
        subsystem.destroy_actor(old)
    d = offset(index)
    made = {}
    for name, actor in source.items():
        copy = subsystem.duplicate_actor(actor, None, unreal.Vector(0, 0, 0))
        if copy is None:
            raise RuntimeError('Could not duplicate ' + name)
        if copy.get_attach_parent_actor() is not None:
            copy.detach_from_actor(detach, detach, detach)
        copy.set_actor_transform(_shift(actor.get_actor_transform(), d), False, True)
        copy.set_actor_label(label(index, name))
        copy.set_folder_path('MakerFleet/T%02d' % index)
        copy.set_editor_property('tags', tags)
        made[name] = copy
    for name, actor in source.items():
        parent = actor.get_attach_parent_actor()
        if parent is not None and parent.get_actor_label() in made:
            if not made[name].attach_to_actor(made[parent.get_actor_label()], '', keep, keep, keep, False):
                raise RuntimeError('Could not attach replica ' + name)
    return {label(index, n): a for n, a in made.items()}


LIGHT_DRAW_DISTANCE_CM = 1500.0
LIGHT_FADE_RANGE_CM = 300.0


def tune_lights(index, existing):
    """Cull replica lights more than 15 m from the viewer (editor viewport or capture).

    Every booth camera is within ~4 m of its own booth's lights, so policy images
    are unchanged; the editor stops paying for ~70 shadowed lights it cannot see
    through the booth walls. The original T00 lights are never changed."""
    if index == 0:
        return 0
    changed = 0
    prefix = label(index, '')
    for name, actor in existing.items():
        if not name.startswith(prefix):
            continue
        component = actor.get_component_by_class(unreal.LightComponent) if isinstance(actor, unreal.Light) else None
        if component is None:
            continue
        draw_distance = 1800.0 if name.endswith('MakerLighting_LocalPointFill') else LIGHT_DRAW_DISTANCE_CM
        if (abs(component.get_editor_property('max_draw_distance') - draw_distance) < 1
                and abs(component.get_editor_property('max_distance_fade_range') - LIGHT_FADE_RANGE_CM) < 1):
            continue
        actor.modify()
        component.modify()
        component.set_editor_property('max_draw_distance', draw_distance)
        component.set_editor_property('max_distance_fade_range', LIGHT_FADE_RANGE_CM)
        changed += 1
    return changed


def _sign(index, text, existing, local=False):
    from maker_arm_grid_scene import number_sticker
    number_sticker(index, local=local)
    return 2


def table_text(index, name):
    return 'T%02d  %s' % (index, name)


def label_main(text='T00  main table (Dream web worker)'):
    """Shared: label the original table. Adds two text actors; changes nothing else."""
    _check_idle()
    return {'label_edits': _sign(0, text, _actors())}


def _apply_variant(variant, existing, local=False):
    index = variant['index']
    state = variant['initial_state']
    moved = sum(set_pose(index, side, state['q_'+side], state['gap_'+side+'_m'], existing) for side in BASES)
    target, target_moved = _place_target(index, variant, existing)
    for spec in variant.get('objects', []):
        actor = existing.get(label(index, spec['label']))
        if actor is None:
            raise ValueError('Missing randomized prop: ' + spec['label'])
        actor.modify()
        rotation = actor.get_actor_rotation()
        pitch, yaw, roll = spec.get('rotation_deg') or [rotation.pitch, spec['yaw_deg'], rotation.roll]
        actor.set_actor_rotation(unreal.Rotator(pitch=pitch, yaw=yaw, roll=roll), False)
        # Rest on the actual top using actual mesh bounds; variants can differ in height.
        extent = actor.get_actor_bounds(False)[1]
        d = offset(index)
        actor.set_actor_location(unreal.Vector(spec['location_cm'][0]+d[0], spec['location_cm'][1]+d[1], 75+extent.z), False, True)
    signs = _sign(index, table_text(index, variant['name']), existing, local)
    lights = tune_lights(index, existing)
    verify(index, state, existing)
    return {'index': index, 'name': variant['name'], 'offset_cm': list(offset(index)),
            'links_moved': moved, 'target_cm': target, 'target_moved': target_moved, 'label_edits': signs,
            'lights_tuned': lights}


def _source_or_fail():
    source = sources()
    need = ['MakerArm_'+s+'_'+l for s in BASES for l in VISUAL_LINKS] + list(CAMERA_ROLES.values())
    missing = [n for n in need if n not in source]
    if missing:
        raise ValueError('Original station incomplete; missing ' + ', '.join(missing[:6]))
    return source


def build_table(config_path, index, force=False):
    """Shared: create booth `index` in the caller's run_python transaction.

    Skips an existing complete booth unless force=True. One table per call keeps
    each Multi-User transaction bounded (about 76 actors)."""
    _check_idle()
    variants = {v['index']: v for v in normalize_config(json.loads(Path(config_path).read_text()))}
    variant = variants[index]
    source = _source_or_fail()
    if index == 0:
        return {'index': 0, 'built': False, 'actors': len(source), 'reason': 'original station reused'}
    existing = fleet_actors(index)
    if existing and not force:
        names = {a.get_actor_label() for a in existing}
        if all(label(index, n) in names for n in source):
            return {'index': index, 'built': False, 'actors': len(existing), 'reason': 'already present'}
    made = _duplicate_table(index, source, local=False)
    report = _apply_variant(variant, made)
    report.update(built=True, actors=len(made))
    return report


def reset(config_path):
    """Shared: put every table's arms and target block in its configured start pose.

    Writes only actors that differ, so an unchanged fleet causes no edits."""
    _check_idle()
    existing = _actors()
    return {'tables': [_apply_variant(v, existing) for v in normalize_config(json.loads(Path(config_path).read_text()))]}


def setup_local(config_path):
    """Editor-only scratch copies of every table (cancelled transaction; not shared)."""
    _check_idle()
    variants = normalize_config(json.loads(Path(config_path).read_text()))
    source = _source_or_fail()
    if any(v['index'] == 0 for v in variants):
        raise ValueError('Local scratch setup cannot include the shared original table')

    def build():
        return [_apply_variant(v, _duplicate_table(v['index'], source, local=True), local=True) for v in variants]
    tables = _locally(build)
    return {'tables': tables, 'replica_actors': len(fleet_actors()), 'scope': 'local-only'}


def clear(local=False):
    """Remove replica actors. Shared mode runs in the caller's transaction."""
    _check_idle()
    actors = [a for a in fleet_actors() if (_local_tag() in _tags(a)) == local]
    if local:
        _locally(lambda: [_subsystem().destroy_actor(a) for a in actors])
    else:
        for actor in actors:
            _subsystem().destroy_actor(actor)
    return {'removed': len(actors), 'remaining': len(fleet_actors())}


def describe():
    tables = {}
    for actor in fleet_actors():
        parsed = parse_label(actor.get_actor_label())
        if parsed:
            key = 'T%02d' % parsed[0]
            entry = tables.setdefault(key, {'actors': 0, 'local': 0})
            entry['actors'] += 1
            entry['local'] += _local_tag() in _tags(actor)
    return {'tables': dict(sorted(tables.items())), 'total': sum(t['actors'] for t in tables.values())}
