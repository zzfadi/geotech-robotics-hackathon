"""Move one existing station into its grid cell inside a scoped CoPresence task.

Call migrate_table once per transaction while simulations are idle. Source CAD,
robot rotations, scales, and table-local coordinates must remain unchanged.
The fleet layout's offset() must use maker_arm_grid.offset before future runs.
"""
import unreal
import maker_arm_grid as grid
import maker_arm_fleet as fleet


def _xyz(v):
    return (v.x, v.y, v.z)


def number_sticker(index):
    """A flush tabletop number; stable internal T00..T10 become visible 1..11."""
    fleet._check_idle()
    d = grid.offset(index)
    actors = fleet._actors()
    subsystem = fleet._subsystem()
    prefix = 'MakerFleet_T%02d__' % index
    for suffix in ('Label_Front', 'Label_Back'):
        old = actors.get(prefix + suffix)
        if old is not None:
            subsystem.destroy_actor(old)
    tags = [unreal.Name(fleet.FLEET_TAG), unreal.Name('MakerTableNumber')]
    if index:
        tags.append(unreal.Name(fleet.table_tag(index)))
    for suffix, kind, height in (('NumberSticker', unreal.StaticMeshActor, 75.06),
                                 ('NumberText', unreal.TextRenderActor, 75.13)):
        name = prefix + suffix
        a = actors.get(name)
        if a is None:
            a = subsystem.spawn_actor_from_class(kind, unreal.Vector(70+d[0], -70+d[1], height))
            a.set_actor_label(name)
            a.set_folder_path('MakerFleet/T%02d' % index)
            a.set_editor_property('tags', tags)
        a.modify()
        a.set_actor_location(unreal.Vector(70+d[0], -70+d[1], height), False, True)
        if suffix == 'NumberSticker':
            c = a.static_mesh_component
            c.set_static_mesh(unreal.load_asset('/Engine/BasicShapes/Cube.Cube'))
            c.set_material(0, unreal.load_asset('/Engine/BasicShapes/BasicShapeMaterial'))
            c.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
            c.set_cast_shadow(False)
            a.set_actor_scale3d(unreal.Vector(.20, .20, .001))
        else:
            a.set_actor_rotation(unreal.Rotator(pitch=90, yaw=0, roll=0), False)
            c = a.text_render
            c.set_text(str(index+1))
            c.set_font(unreal.load_asset('/Engine/EngineFonts/RobotoDistanceField'))
            c.set_world_size(13)
            c.set_horizontal_alignment(unreal.HorizTextAligment.EHTA_CENTER)
            c.set_vertical_alignment(unreal.VerticalTextAligment.EVRTA_TEXT_CENTER)
            c.set_text_render_color(unreal.Color(0, 0, 0, 255))
            c.set_cast_shadow(False)
            for icon in a.get_components_by_class(unreal.BillboardComponent):
                icon.set_visibility(False)
    return {'table_id': index, 'sticker': str(index+1)}


def brighten_global():
    """Shared ambient fill plus daylight, with existing table lights preserved."""
    fleet._check_idle()
    actors = fleet._actors()
    ambient = actors.get('MakerLighting_Ambient')
    if ambient is None:
        ambient = fleet._subsystem().spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 600))
        ambient.set_actor_label('MakerLighting_Ambient')
    ambient.modify()
    component = ambient.light_component
    component.modify()
    component.set_editor_property('source_type', unreal.SkyLightSourceType.SLS_SPECIFIED_CUBEMAP)
    component.set_editor_property('cubemap', unreal.load_asset('/Engine/MapTemplates/Sky/DaylightAmbientCubemap'))
    component.set_intensity(2.0)
    component.set_editor_property('lower_hemisphere_is_black', False)
    sun = actors.get('MakerLighting_GlobalDaylight')
    if sun is None:
        sun = fleet._subsystem().spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 1000))
        sun.set_actor_label('MakerLighting_GlobalDaylight')
        sun.set_folder_path('MakerStation/Environment')
    sun.modify()
    sun.set_actor_rotation(unreal.Rotator(pitch=-45, yaw=-35, roll=0), False)
    light = sun.light_component
    light.modify()
    light.set_intensity(2500.0)
    light.set_editor_property('light_source_angle', 3.0)
    light.set_cast_shadows(True)
    return {'ambient_intensity': component.intensity, 'daylight_lux': light.intensity}


def migrate_table(index):
    grid.offset(index)  # validate before any edits
    fleet._check_idle()
    all_actors = fleet._actors()
    prefix = '' if index == 0 else fleet.label(index, '')
    floor_name = prefix + 'MakerEnvironment_Floor'
    floor = all_actors.get(floor_name)
    if floor is None:
        raise RuntimeError('Missing floor: ' + floor_name)
    if index:
        actors = {a.get_actor_label(): a for a in fleet.fleet_actors(index)}
    else:
        actors = fleet.sources()
        for name, actor in all_actors.items():
            if name.startswith('MakerEnvironment_') or name.startswith('MakerFleet_T00__Label_'):
                actors[name] = actor
    before = {name: (_xyz(a.get_actor_location()), _xyz(a.get_actor_scale3d()))
              for name, a in actors.items() if 'MakerArm_' in name or 'MakerStation_' in name}
    delta = grid.translation(index, _xyz(floor.get_actor_location()))
    moved = 0
    if max(abs(v) for v in delta) > .001:
        # Attached cameras travel with their parent; move only room roots.
        for actor in actors.values():
            parent = actor.get_attach_parent_actor()
            if parent is not None and parent.get_actor_label() in actors:
                continue
            actor.modify()
            p = _xyz(actor.get_actor_location())
            actor.set_actor_location(unreal.Vector(*(p[i]+delta[i] for i in range(3))), False, True)
            moved += 1
    subsystem = fleet._subsystem()
    roof = actors.get(prefix + 'MakerEnvironment_Roof')
    if roof is not None:
        subsystem.destroy_actor(roof)
    cube = unreal.load_asset('/Engine/BasicShapes/Cube.Cube')
    plaster = unreal.load_asset('/Game/_Agents/Denys_Mac_Codex/Environment/M_HallPlaster')
    surfaces = 0
    for name, spec in grid.shell(index).items():
        label = prefix + 'MakerEnvironment_' + name
        actor = actors.get(label)
        if actor is None:
            actor = subsystem.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(*spec['location']))
            actor.set_actor_label(label)
            actor.set_folder_path('MakerFleet/T%02d' % index if index else 'MakerStation/Environment')
            if index:
                actor.set_editor_property('tags', [unreal.Name(fleet.FLEET_TAG), unreal.Name(fleet.table_tag(index))])
            actor.static_mesh_component.set_static_mesh(cube)
            if plaster:
                actor.static_mesh_component.set_material(0, plaster)
            actors[label] = actor
        if not isinstance(actor, unreal.StaticMeshActor):
            raise RuntimeError('Room shell is not a static mesh: ' + label)
        if (max(abs(a-b) for a,b in zip(_xyz(actor.get_actor_location()), spec['location'])) > .001
                or max(abs(a-b) for a,b in zip(_xyz(actor.get_actor_scale3d()), spec['scale'])) > .001):
            actor.modify()
            actor.set_actor_location(unreal.Vector(*spec['location']), False, True)
            actor.set_actor_scale3d(unreal.Vector(*spec['scale']))
            surfaces += 1
    for name, (p, scale) in before.items():
        a = actors[name]
        if max(abs(v-(p[i]+delta[i])) for i,v in enumerate(_xyz(a.get_actor_location()))) > .01:
            raise RuntimeError('Table-local pose changed during grid migration: ' + name)
        if max(abs(x-y) for x,y in zip(scale, _xyz(a.get_actor_scale3d()))) > .00001:
            raise RuntimeError('Protected mesh scale changed: ' + name)
    return {'table': index, 'offset_cm': grid.offset(index), 'translated_roots': moved,
            'shell_updates': surfaces, 'protected_actors_verified': len(before)}
